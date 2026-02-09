import { useSession } from '../contexts/SessionContext';
import { useTerminal } from '../hooks/useTerminal';

export function TerminalView() {
  const { session, wsUrl, destroySession } = useSession();
  const { containerRef, authUrl, dismissAuth, pasteClipboard } = useTerminal(wsUrl);

  return (
    <div className="terminal-container">
      <div className="terminal-header">
        <div className="session-info">
          <span className="status-dot" />
          <span>Session</span>
          <span className="session-id">{session?.session_id}</span>
        </div>
        <button className="btn btn-danger" onClick={destroySession}>
          End Session
        </button>
      </div>
      {authUrl && (
        <div className="auth-banner">
          <span className="auth-label">&#x1f511; Authentication required</span>
          <a className="auth-link" href={authUrl} target="_blank" rel="noopener noreferrer">
            Open login &rarr;
          </a>
          <button className="auth-paste" onClick={pasteClipboard}>
            Paste code
          </button>
          <button className="auth-dismiss" onClick={dismissAuth}>&times;</button>
        </div>
      )}
      <div className="terminal-body" ref={containerRef} />
    </div>
  );
}
