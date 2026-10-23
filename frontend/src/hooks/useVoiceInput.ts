import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Voice input hook preferring local Whisper (/api/v1/voice/transcribe via MediaRecorder)
 * with opt-in Web Speech API (cloud) fallback (Epic #2591, #2594, Panel th_8820201930a3).
 */

interface SpeechRecognitionAlternative {
  readonly transcript: string;
  readonly confidence: number;
}
interface SpeechRecognitionResult {
  readonly length: number;
  readonly isFinal: boolean;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  readonly results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}
interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

interface SpeechRecognitionCtor {
  new (): SpeechRecognition;
}

export type VoiceEngineMode = 'local-whisper' | 'web-speech-cloud' | 'none';

export interface UseVoiceInputOptions {
  lang?: string;
  mode?: 'local' | 'cloud';
  endpoint?: string;
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
}

export interface UseVoiceInputResult {
  available: boolean;
  recording: boolean;
  transcribing: boolean;
  engine: VoiceEngineMode;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

function hasLocalMediaRecorder(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;
  return (
    typeof window.MediaRecorder !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function'
  );
}

export function useVoiceInput({
  lang = 'en-US',
  mode = 'local',
  endpoint = '/api/v1/voice/transcribe',
  onTranscript,
  onError,
}: UseVoiceInputOptions): UseVoiceInputResult {
  const SpeechRecognitionCtor = getSpeechRecognitionCtor();
  const canRecordLocal = mode !== 'cloud' && hasLocalMediaRecorder();
  const canUseCloud = SpeechRecognitionCtor !== undefined;
  const engine: VoiceEngineMode = canRecordLocal
    ? 'local-whisper'
    : canUseCloud
      ? 'web-speech-cloud'
      : 'none';
  const available = engine !== 'none';

  const recognizerRef = useRef<SpeechRecognition | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const releaseStream = useCallback(() => {
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        track.stop();
      }
      streamRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
    }
    if (recognizerRef.current) {
      recognizerRef.current.stop();
      recognizerRef.current = null;
    }
    setRecording(false);
  }, []);

  const startLocalWhisper = useCallback(async () => {
    if (mediaRecorderRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mimeType =
        typeof MediaRecorder.isTypeSupported === 'function' &&
        MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : 'audio/webm';
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };

      recorder.onstop = async () => {
        releaseStream();
        mediaRecorderRef.current = null;
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType });
        chunksRef.current = [];
        if (blob.size === 0) return;

        setTranscribing(true);
        try {
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: {
              'Content-Type': (blob.type || 'audio/webm').split(';')[0],
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: blob,
          });
          if (!res.ok) {
            const errBody = await res.json().catch(() => ({}));
            const detail =
              errBody?.detail?.detail ||
              errBody?.detail ||
              `Local Whisper error (${res.status})`;
            onErrorRef.current?.(String(detail));
            return;
          }
          const data = (await res.json()) as { text?: string };
          const text = (data.text ?? '').trim();
          if (text) onTranscriptRef.current(text);
        } catch (err) {
          onErrorRef.current?.(
            err instanceof Error ? err.message : 'Local Whisper transcription failed.'
          );
        } finally {
          setTranscribing(false);
        }
      };

      recorder.start();
      setRecording(true);
    } catch {
      releaseStream();
      setRecording(false);
      onErrorRef.current?.('Microphone permission denied or unavailable.');
    }
  }, [endpoint, releaseStream]);

  const start = useCallback(() => {
    if (canRecordLocal) {
      void startLocalWhisper();
      return;
    }
    if (SpeechRecognitionCtor === undefined) {
      onErrorRef.current?.('Voice input is not supported in this browser.');
      return;
    }
    if (recognizerRef.current) return;

    const rec = new SpeechRecognitionCtor();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = lang;

    rec.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = Array.from(event.results)
        .map((r) => r[0].transcript)
        .join(' ')
        .trim();
      if (transcript) onTranscriptRef.current(transcript);
    };

    rec.onerror = (event: SpeechRecognitionErrorEvent) => {
      const msgs: Record<string, string> = {
        'not-allowed': 'Microphone permission denied.',
        'no-speech': 'No speech detected — try again.',
        'audio-capture': 'No microphone found.',
        network: 'Network error during speech recognition.',
      };
      onErrorRef.current?.(msgs[event.error] ?? `Speech error: ${event.error}`);
      recognizerRef.current = null;
      setRecording(false);
    };

    rec.onend = () => {
      recognizerRef.current = null;
      setRecording(false);
    };

    recognizerRef.current = rec;
    rec.start();
    setRecording(true);
  }, [SpeechRecognitionCtor, canRecordLocal, lang, startLocalWhisper]);

  const toggle = useCallback(() => {
    if (recording) stop();
    else start();
  }, [recording, start, stop]);

  useEffect(() => {
    return () => {
      recognizerRef.current?.stop();
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      releaseStream();
    };
  }, [releaseStream]);

  return { available, recording, transcribing, engine, start, stop, toggle };
}
