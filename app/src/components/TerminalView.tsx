import { useSession } from '../contexts/SessionContext';
import { useTerminal } from '../hooks/useTerminal';

export function TerminalView() {
  const { session, wsUrl, destroySession } = useSession();
  const { containerRef, authUrl, dismissAuth, sendText } = useTerminal(wsUrl);

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) sendText(text);
    } catch {
      // Clipboard API denied
    }
  };

const handlePasteCode = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text.trim()) {
        sendText(text.trim() + '\n');
        dismissAuth();
      }
    } catch {
      // Clipboard API denied
    }
  };

  return (
    <div className="terminal-container">
      <div className="terminal-header">
        <div className="session-info">
          <span className="status-dot" />
          <span className="session-id">{session?.session_id}</span>
        </div>
        <button className="btn btn-danger" onClick={destroySession}>
          End Session
        </button>
      </div>
      {authUrl && (
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
        <button className="toolbar-btn" onClick={handlePaste} title="Paste from clipboard">
          Paste
        </button>
        <span className="toolbar-sep" />
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x1b')}
          title="Send Escape"
        >
          Esc
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x02\x1b[5~')}
          title="Scroll up (tmux scrollback)"
        >
          &#x25B2;
        </button>
        <button
          className="toolbar-btn"
          onClick={() => sendText('\x1b[6~')}
          title="Scroll down"
        >
          &#x25BC;
        </button>
      </div>
    </div>
  );
}
