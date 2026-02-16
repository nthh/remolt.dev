import { useState, useEffect } from 'react';
import { useTerminal } from '../hooks/useTerminal';

interface TerminalPanelProps {
  wsUrl: string | null;
  visible: boolean;
}

export function TerminalPanel({ wsUrl, visible }: TerminalPanelProps) {
  const [copyOnSelect, setCopyOnSelect] = useState(() => {
    const stored = localStorage.getItem('remolt:copyOnSelect');
    return stored === null ? true : stored === 'true';
  });

  useEffect(() => {
    const handler = () => {
      const stored = localStorage.getItem('remolt:copyOnSelect');
      setCopyOnSelect(stored === null ? true : stored === 'true');
    };
    window.addEventListener('remolt:settingsChanged', handler);
    return () => window.removeEventListener('remolt:settingsChanged', handler);
  }, []);

  const { containerRef, authUrl, dismissAuth, sendText } = useTerminal(wsUrl, { copyOnSelect });

  const handlePaste = async () => {
    let text: string | null = null;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      // Clipboard API denied — fall through to prompt
    }
    if (!text) {
      text = window.prompt('Paste here:');
    }
    if (text) {
      sendText(text);
    }
  };

  const handlePasteCode = async () => {
    let text: string | null = null;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      // Clipboard API denied — fall through to prompt
    }
    if (!text) {
      text = window.prompt('Paste code here:');
    }
    if (text && text.trim()) {
      sendText(text.trim() + '\n');
      dismissAuth();
    }
  };

  return (
    <div className={`workspace-panel${visible ? '' : ' workspace-panel-hidden'}`}>
      {authUrl && visible && (
        <div className="auth-banner">
          <span className="auth-label">auth required</span>
          <a className="auth-link" href={authUrl} target="_blank" rel="noopener noreferrer">
            Open login &rarr;
          </a>
          <button className="auth-paste" onClick={handlePasteCode}>
            Paste code
          </button>
          <button className="auth-dismiss" onClick={dismissAuth}>&times;</button>
        </div>
      )}
      <div className="terminal-body" ref={containerRef} />
      <div className="terminal-bottombar">
        <button className="toolbar-btn" onClick={handlePaste} data-tooltip="Paste from clipboard">
          Paste
        </button>
        <span className="toolbar-sep" />
        <button className="toolbar-btn" onClick={() => sendText('\x1b')} data-tooltip="Send Escape key">
          Esc
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x02\x1b[5~')}
          data-tooltip="Scroll up (tmux)"
        >
          &#x25B2;
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x1b[6~')}
          data-tooltip="Scroll down"
        >
          &#x25BC;
        </button>
        <span className="toolbar-sep" />
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x02%')}
          data-tooltip="Split vertical"
        >
          &#x2502;
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x02"')}
          data-tooltip="Split horizontal"
        >
          &#x2500;
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x02o')}
          data-tooltip="Switch pane"
        >
          &#x21E5;
        </button>
        <button
          className="toolbar-btn toolbar-btn-danger"
          onClick={() => sendText('\x02x')}
          data-tooltip="Close pane"
        >
          &#x2715;
        </button>
      </div>
    </div>
  );
}
