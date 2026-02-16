import { useState } from 'react';
import { useSession } from '../contexts/SessionContext';
import { useTerminal } from '../hooks/useTerminal';

type Tab = 'terminal' | 'dashboard';

export function TerminalView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { session, wsUrl, destroySession } = useSession();
  const { containerRef, authUrl, dismissAuth, sendText } = useTerminal(wsUrl);
  const hasDashboard = !!session?.proxy_url;
  const [activeTab, setActiveTab] = useState<Tab>('terminal');

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
    <div className="terminal-container">
      <div className="terminal-header">
        <div className="session-info">
          <span className="status-dot" />
          {hasDashboard ? (
            <div className="session-tabs">
              <button
                className={`session-tab ${activeTab === 'terminal' ? 'active' : ''}`}
                onClick={() => setActiveTab('terminal')}
              >
                Terminal
              </button>
              <button
                className={`session-tab ${activeTab === 'dashboard' ? 'active' : ''}`}
                onClick={() => setActiveTab('dashboard')}
              >
                Dashboard
              </button>
            </div>
          ) : (
            <span className="session-id">{session?.session_id}</span>
          )}
          <span className="version-tag">{__APP_VERSION__}</span>
        </div>
        <div className="terminal-actions">
          <button className="btn btn-icon" onClick={onOpenSettings} title="Settings">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button className="btn btn-danger" onClick={destroySession}>
            End Session
          </button>
        </div>
      </div>
      {authUrl && activeTab === 'terminal' && (
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
      <div
        className="terminal-body"
        ref={containerRef}
        style={{ display: activeTab === 'terminal' ? '' : 'none' }}
      />
      {hasDashboard && (
        <div
          className="dashboard-frame"
          style={{ display: activeTab === 'dashboard' ? '' : 'none' }}
        >
          <iframe
            src={session!.proxy_url!}
            title="Agent Dashboard"
          />
        </div>
      )}
      {activeTab === 'terminal' && (
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
      )}
    </div>
  );
}
