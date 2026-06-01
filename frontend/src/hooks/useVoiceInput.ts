import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Voice input hook using the Web Speech API (SpeechRecognition).
 *
 * - Starts/stops continuous recognition.
 * - Calls `onTranscript` with the final, trimmed text when speech ends.
 * - Calls `onError` with a human-readable message on failure.
 * - Degrades gracefully: `available` is false on unsupported browsers.
 *
 * Runner_Dashboard issue #623.
 */

// Minimal module-local typings for the Web Speech API. These interfaces are not
// part of the standard TS DOM lib, so we declare just the surface this hook
// uses. They are intentionally NOT global to avoid clashing with the similar
// local declarations in components/VoiceInputButton.tsx.
// (Web Speech API is non-standard; supported in Chrome/Edge.)
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

export interface UseVoiceInputOptions {
  lang?: string;
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
}

export interface UseVoiceInputResult {
  available: boolean;
  recording: boolean;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  // `SpeechRecognition` is non-standard and absent from lib.dom; read it via a
  // narrow local cast rather than a global Window augmentation (which would
  // collide with the local declarations in components/VoiceInputButton.tsx).
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

export function useVoiceInput({
  lang = 'en-US',
  onTranscript,
  onError,
}: UseVoiceInputOptions): UseVoiceInputResult {
  const SpeechRecognitionCtor: SpeechRecognitionCtor | undefined =
    getSpeechRecognitionCtor();
  const available = SpeechRecognitionCtor !== undefined;

  const recognizerRef = useRef<SpeechRecognition | null>(null);
  const [recording, setRecording] = useState(false);

  // Keep callbacks stable so the recognizer event handlers don't go stale.
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const stop = useCallback(() => {
    if (recognizerRef.current) {
      recognizerRef.current.stop();
      recognizerRef.current = null;
    }
    setRecording(false);
  }, []);

  const start = useCallback(() => {
    if (SpeechRecognitionCtor === undefined) {
      onErrorRef.current?.(
        'Voice input is not supported in this browser. Try Chrome or Edge.'
      );
      return;
    }
    if (recognizerRef.current) return; // already recording

    const rec = new SpeechRecognitionCtor();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = lang;

    rec.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = Array.from(event.results)
        .map((r) => r[0].transcript)
        .join(' ')
        .trim();
      if (transcript) {
        onTranscriptRef.current(transcript);
      }
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
  }, [SpeechRecognitionCtor, lang]);

  const toggle = useCallback(() => {
    if (recording) {
      stop();
    } else {
      start();
    }
  }, [recording, start, stop]);

  // Clean up on unmount.
  useEffect(() => {
    return () => {
      if (recognizerRef.current) {
        recognizerRef.current.stop();
        recognizerRef.current = null;
      }
    };
  }, []);

  return { available, recording, start, stop, toggle };
}
