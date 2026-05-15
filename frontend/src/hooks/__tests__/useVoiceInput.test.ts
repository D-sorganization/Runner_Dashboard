import { act, renderHook } from '@testing-library/react';
import { useVoiceInput } from '../useVoiceInput';

// ---------------------------------------------------------------------------
// Minimal SpeechRecognition mock
// ---------------------------------------------------------------------------

class MockSpeechRecognition {
  continuous = false;
  interimResults = false;
  lang = 'en-US';

  onresult: ((event: SpeechRecognitionEvent) => void) | null = null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null = null;
  onend: (() => void) | null = null;

  start = jest.fn(() => {
    // noop — test triggers events manually
  });
  stop = jest.fn(() => {
    this.onend?.();
  });
}

let mockInstance: MockSpeechRecognition | null = null;

function installMock() {
  mockInstance = null;
  const Ctor = jest.fn(() => {
    mockInstance = new MockSpeechRecognition();
    return mockInstance;
  }) as unknown as typeof SpeechRecognition;
  Object.defineProperty(window, 'SpeechRecognition', {
    value: Ctor,
    writable: true,
    configurable: true,
  });
}

function removeMock() {
  Object.defineProperty(window, 'SpeechRecognition', {
    value: undefined,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(window, 'webkitSpeechRecognition', {
    value: undefined,
    writable: true,
    configurable: true,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useVoiceInput — availability', () => {
  afterEach(removeMock);

  it('available is false when SpeechRecognition is absent', () => {
    removeMock();
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    expect(result.current.available).toBe(false);
  });

  it('available is true when SpeechRecognition is present', () => {
    installMock();
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    expect(result.current.available).toBe(true);
  });
});

describe('useVoiceInput — start / stop', () => {
  beforeEach(installMock);
  afterEach(removeMock);

  it('recording is false initially', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    expect(result.current.recording).toBe(false);
  });

  it('recording becomes true after start()', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    act(() => result.current.start());
    expect(result.current.recording).toBe(true);
    expect(mockInstance?.start).toHaveBeenCalledTimes(1);
  });

  it('recording becomes false after stop()', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    act(() => result.current.start());
    act(() => result.current.stop());
    expect(result.current.recording).toBe(false);
    expect(mockInstance?.stop).toHaveBeenCalled();
  });

  it('toggle starts then stops recording', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    act(() => result.current.toggle());
    expect(result.current.recording).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.recording).toBe(false);
  });
});

describe('useVoiceInput — transcription', () => {
  beforeEach(installMock);
  afterEach(removeMock);

  it('calls onTranscript with the recognized text', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    act(() => result.current.start());

    const fakeResult = [{ transcript: 'hello maxwell', confidence: 0.95 }];
    const fakeEvent = {
      results: { length: 1, 0: { 0: fakeResult[0], isFinal: true } },
    } as unknown as SpeechRecognitionEvent;

    act(() => mockInstance?.onresult?.(fakeEvent));
    expect(onTranscript).toHaveBeenCalledWith('hello maxwell');
  });

  it('recording resets to false after recognition ends', () => {
    const onTranscript = jest.fn();
    const { result } = renderHook(() => useVoiceInput({ onTranscript }));
    act(() => result.current.start());
    act(() => mockInstance?.onend?.());
    expect(result.current.recording).toBe(false);
  });
});

describe('useVoiceInput — error handling', () => {
  beforeEach(installMock);
  afterEach(removeMock);

  it('calls onError with friendly message on not-allowed', () => {
    const onTranscript = jest.fn();
    const onError = jest.fn();
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript, onError })
    );
    act(() => result.current.start());

    const fakeError = { error: 'not-allowed' } as SpeechRecognitionErrorEvent;
    act(() => mockInstance?.onerror?.(fakeError));
    expect(onError).toHaveBeenCalledWith('Microphone permission denied.');
    expect(result.current.recording).toBe(false);
  });

  it('calls onError when SR unavailable and start() is called', () => {
    removeMock();
    const onTranscript = jest.fn();
    const onError = jest.fn();
    const { result } = renderHook(() =>
      useVoiceInput({ onTranscript, onError })
    );
    act(() => result.current.start());
    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining('not supported')
    );
  });
});
