import { useCallback, useEffect, useRef, useState } from 'react'

/** Languages a complainant can dictate an FIR in (browser speech recognition, Chrome/Edge). */
export const DICTATION_LANGUAGES = [
  { code: 'hi-IN', label: 'हिन्दी' },
  { code: 'en-IN', label: 'English' },
  { code: 'bn-IN', label: 'বাংলা' },
  { code: 'mr-IN', label: 'मराठी' },
  { code: 'ta-IN', label: 'தமிழ்' },
  { code: 'te-IN', label: 'తెలుగు' },
  { code: 'gu-IN', label: 'ગુજરાતી' },
  { code: 'kn-IN', label: 'ಕನ್ನಡ' },
  { code: 'ml-IN', label: 'മലയാളം' },
  { code: 'pa-IN', label: 'ਪੰਜਾਬੀ' },
  { code: 'ur-IN', label: 'اردو' },
] as const

interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: RecognitionEvent) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/**
 * Speech to text. `onFinal` receives each finished sentence; `interim` is the words still being heard.
 */
export function useDictation(onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recRef = useRef<Recognition | null>(null)
  const finalRef = useRef(onFinal)
  finalRef.current = onFinal
  const supported = typeof window !== 'undefined' && recognitionCtor() !== null

  const stop = useCallback(() => {
    recRef.current?.stop()
  }, [])

  const start = useCallback((lang: string) => {
    const Ctor = recognitionCtor()
    if (!Ctor) { setError('Voice input needs Chrome or Edge.'); return }
    recRef.current?.stop()
    const rec = new Ctor()
    rec.lang = lang
    rec.continuous = true
    rec.interimResults = true
    rec.onresult = (e) => {
      let pending = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        if (r.isFinal) finalRef.current(r[0].transcript.trim())
        else pending += r[0].transcript
      }
      setInterim(pending)
    }
    rec.onerror = (e) => {
      setError(e.error === 'not-allowed' ? 'Microphone permission was denied.' : e.error === 'network' ? 'Voice input needs internet.' : null)
    }
    rec.onend = () => { setListening(false); setInterim('') }
    recRef.current = rec
    setError(null)
    rec.start()
    setListening(true)
  }, [])

  useEffect(() => () => recRef.current?.stop(), [])

  return { supported, listening, interim, error, start, stop }
}
