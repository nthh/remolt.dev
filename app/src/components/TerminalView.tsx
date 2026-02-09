import { useState, useRef, useEffect } from 'react';
import { useSession } from '../contexts/SessionContext';
import { useTerminal } from '../hooks/useTerminal';

export function TerminalView() {
  const { session, wsUrl, destroySession } = useSession();
  const { containerRef, authUrl, dismissAuth, sendText } = useTerminal(wsUrl);
  const [showCodeInput, setShowCodeInput] = useState(false);
  const [code, setCode] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (showCodeInput) inputRef.current?.focus();
  }, [showCodeInput]);

  const handleSubmitCode = () => {
    if (code.trim()) {
      sendText(code.trim() + '\n');
      setCode('');
      setShowCodeInput(false);
    }
  };

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
          {showCodeInput ? (
            <form onSubmit={(e) => { e.preventDefault(); handleSubmitCode(); }} style={{ display: 'flex', gap: '6px' }}>
              <input
                ref={inputRef}
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Paste code here"
                style={{
                  padding: '4px 8px',
                  background: 'var(--bg)',
                  border: '1px solid var(--accent)',
                  borderRadius: '4px',
                  color: 'var(--fg)',
                  fontSize: '13px',
                  fontFamily: "'JetBrains Mono', monospace",
                  width: '180px',
                }}
              />
              <button type="submit" className="auth-paste">Submit</button>
            </form>
          ) : (
            <button className="auth-paste" onClick={() => setShowCodeInput(true)}>
              Paste code
            </button>
          )}
          <button className="auth-dismiss" onClick={dismissAuth}>&times;</button>
        </div>
      )}
      <div className="terminal-body" ref={containerRef} />
    </div>
  );
}
