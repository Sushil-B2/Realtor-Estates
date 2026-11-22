"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { getProject, summarize } from "@/lib/projects";
import LeadCapture from "./LeadCapture";

type ChatEntry = { role: "user" | "assistant"; content: string; needsHuman?: boolean };
type SpeechResult = ArrayLike<ArrayLike<{ transcript: string }>>;
type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: { results: SpeechResult }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => SpeechRecognitionLike;
  webkitSpeechRecognition?: new () => SpeechRecognitionLike;
};

export default function AssistantWidget({ project = "samruddhi-estate" }: { project?: string }) {
  const projectInfo = getProject(project);
  const stats = projectInfo ? summarize(projectInfo.layout) : null;
  const starterMessage: ChatEntry = {
    role: "assistant",
    content: projectInfo
      ? `Hi, I’m the guide for ${projectInfo.name}. The sheet shows ${stats?.total ?? 0} plots — ${stats?.available ?? 0} available*, ${stats?.reserved ?? 0} reserved*, ${stats?.sold ?? 0} sold/marked. Ask about a plot, facing, roads or a site visit.`
      : "Hi, I’m the site guide. Ask about the plans, plot sizes, location context or a site visit. I’ll tell you when a detail needs a human check.",
  };
  const quickQuestions = projectInfo
    ? ["Which plots are still available?", "Which way do the plots face?", "What road widths are shown?"]
    : ["What are the sample plot sizes?", "Which plots are marked sold?", "Where is the project?"];
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceNote, setVoiceNote] = useState("");
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatEntry[]>([starterMessage]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputId = useId();
  const whatsappUrl = process.env.NEXT_PUBLIC_WHATSAPP_URL;

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, open, busy]);

  async function send(text: string) {
    const cleanText = text.trim();
    if (!cleanText || busy) return;
    const userEntry: ChatEntry = { role: "user", content: cleanText };
    const nextMessages = [...messages, userEntry];
    setMessages(nextMessages);
    setDraft("");
    setBusy(true);
    setVoiceNote("");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: cleanText,
          project,
          history: nextMessages.slice(-8).map(({ role, content }) => ({ role, content })),
        }),
      });
      const data = (await response.json()) as { answer?: string; error?: string; needsHuman?: boolean };
      if (!response.ok) throw new Error(data.error || "Please try that again.");
      setMessages((current) => [
        ...current,
        { role: "assistant", content: data.answer || "I couldn't prepare an answer just now.", needsHuman: data.needsHuman },
      ]);
    } catch {
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: "I couldn’t connect just now. You can still leave a request for a person to follow up.",
          needsHuman: true,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(draft);
  }

  function startVoice() {
    const speechWindow = window as SpeechWindow;
    const SpeechRecognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setVoiceNote("Voice input is not supported in this browser. You can type your question instead.");
      return;
    }
    try {
      const recognition = new SpeechRecognition();
      recognition.lang = "en-IN";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results).map((result) => result[0]?.transcript || "").join(" ").trim();
        if (transcript) setDraft((current) => `${current}${current ? " " : ""}${transcript}`);
      };
      recognition.onend = () => setListening(false);
      recognition.onerror = () => {
        setListening(false);
        setVoiceNote("I couldn't hear that clearly. Please try again or type your question.");
      };
      setListening(true);
      setVoiceNote("Listening…");
      recognition.start();
    } catch {
      setListening(false);
      setVoiceNote("Voice input couldn't start. Please type your question instead.");
    }
  }

  return (
    <div className="assistant-widget">
      {open ? (
        <section className="assistant-panel" role="dialog" aria-modal="false" aria-labelledby={`${inputId}-title`}>
          <header className="assistant-panel__header">
            <div className="assistant-avatar" aria-hidden="true">S</div>
            <div>
              <h2 id={`${inputId}-title`}>The site guide</h2>
              <p><span className="assistant-online" /> Replies from the shared project notes</p>
            </div>
            <button className="assistant-close" type="button" aria-label="Close site guide" onClick={() => setOpen(false)}>×</button>
          </header>
          <div className="assistant-messages" role="log" aria-live="polite" aria-relevant="additions text">
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`assistant-message assistant-message--${message.role}`}>
                <p>{message.content}</p>
                {message.needsHuman ? (
                  <div className="assistant-message__handoff">
                    {whatsappUrl ? (
                      <a className="assistant-whatsapp" href={whatsappUrl} target="_blank" rel="noreferrer">Continue to WhatsApp ↗</a>
                    ) : (
                      <LeadCapture label="Request a human reply" interest="callback" className="button button--small button--outline" />
                    )}
                  </div>
                ) : null}
              </div>
            ))}
            {busy ? <div className="assistant-message assistant-message--assistant assistant-typing" aria-label="Preparing an answer"><span /><span /><span /></div> : null}
            <div ref={endRef} />
          </div>
          {messages.length === 1 ? (
            <div className="assistant-quick" aria-label="Suggested questions">
              {quickQuestions.map((question) => (
                <button type="button" key={question} onClick={() => void send(question)} disabled={busy}>{question}</button>
              ))}
            </div>
          ) : null}
          <form className="assistant-input" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor={`${inputId}-message`}>Ask a question</label>
            <textarea
              id={`${inputId}-message`}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask a question…"
              rows={1}
              maxLength={500}
            />
            <button
              className={`assistant-mic${listening ? " is-listening" : ""}`}
              type="button"
              aria-label={listening ? "Listening for your question" : "Ask by voice"}
              onClick={startVoice}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3m-4 0h8" /></svg>
            </button>
            <button className="assistant-send" type="submit" aria-label="Send question" disabled={busy || !draft.trim()}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 8-16 8 3-8-3-8Z" /><path d="M7 12h13" /></svg>
            </button>
          </form>
          {voiceNote ? <p className="assistant-voice-note" role="status">{voiceNote}</p> : null}
          <p className="assistant-footnote">No live inventory or legal advice. Please verify important details independently.</p>
        </section>
      ) : null}
      <button
        className={`assistant-toggle${open ? " is-open" : ""}`}
        type="button"
        aria-expanded={open}
        aria-label={open ? "Close the site guide" : "Open the site guide"}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="assistant-toggle__icon" aria-hidden="true">{open ? "×" : "✦"}</span>
        <span>{open ? "Close guide" : "Ask the site guide"}</span>
        {!open ? <span className="assistant-toggle__ping" aria-hidden="true" /> : null}
      </button>
    </div>
  );
}
