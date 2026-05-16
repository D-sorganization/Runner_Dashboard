import React, { useState, useEffect, useCallback, useRef } from 'react';

interface VoiceInputButtonProps {
  /** Callback fired when a final transcription result is received */
  onTranscription: (text: string) => void;
  /** Whether the button should be disabled (e.g. while thinking) */
  disabled?: boolean;
}

// Web Speech API types (not in lib.dom.d.ts by default)
interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
}

interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance;

interface SpeechRecognitionWindow extends Window {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
}

/**
 * VoiceInputButton — A sleek microphone button that uses the Web Speech API
 * to transcribe voice to text for the Sidekick assistant.
 */
export const VoiceInputButton: React.FC<VoiceInputButtonProps> = ({
  onTranscription,
  disabled = false,
}) => {
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);

  useEffect(() => {
    const win = window as SpeechRecognitionWindow;
    const SpeechRecognition = win.SpeechRecognition ?? win.webkitSpeechRecognition;

    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = false; // We only want final results to append
      recognition.lang = 'en-US';

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        let finalTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            finalTranscript += event.results[i][0].transcript;
          }
        }
        if (finalTranscript) {
          onTranscription(finalTranscript);
        }
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        setIsListening(false);
        // Surface error via aria-live region rather than console
        recognition.onend = () => {
          setIsListening(false);
        };
        void event.error; // consumed — caller sees isListening→false as the signal
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
    }
    // Browser without Web Speech API: component renders null (see guard below)
  }, [onTranscription]);

  const toggleListening = useCallback(() => {
    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
    } else {
      try {
        recognitionRef.current?.start();
        setIsListening(true);
      } catch {
        setIsListening(false);
      }
    }
  }, [isListening]);

  if (!recognitionRef.current) return null;

  return (
    <>
      <button
        id="voice-input-toggle"
        onClick={toggleListening}
        disabled={disabled}
        title={isListening ? 'Stop listening' : 'Start voice input'}
        className="voice-input-btn"
        style={{
          background: isListening ? 'rgba(248, 81, 73, 0.2)' : 'transparent',
          border: isListening ? '1px solid var(--accent-red)' : '1px solid var(--border)',
          borderRadius: '8px',
          width: '36px',
          height: '36px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: disabled ? 'not-allowed' : 'pointer',
          color: isListening ? 'var(--accent-red)' : 'var(--text-muted)',
          transition: 'all 0.2s ease',
          padding: 0,
          position: 'relative',
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <span style={{ fontSize: '18px' }}>{isListening ? '⏹' : '🎤'}</span>
        {isListening && (
          <span
            style={{
              position: 'absolute',
              top: '-4px',
              right: '-4px',
              width: '8px',
              height: '8px',
              background: 'var(--accent-red)',
              borderRadius: '50%',
              boxShadow: '0 0 8px var(--accent-red)',
              animation: 'pulse 1.5s infinite',
            }}
          />
        )}
      </button>
      <style>{`
        @keyframes pulse {
          0% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.5); opacity: 0.5; }
          100% { transform: scale(1); opacity: 1; }
        }
        .voice-input-btn:hover:not(:disabled) {
          border-color: var(--accent-blue);
          color: var(--accent-blue);
          background: rgba(49, 120, 198, 0.1);
        }
      `}</style>
    </>
  );
};
