import { useState, useEffect, useRef, type FormEvent } from 'react';
import { useSession, type AgentInfo } from '../contexts/SessionContext';

const PREFS_KEY = 'remolt:prefs';

interface Prefs {
  gitUserName: string;
  gitUserEmail: string;
  agentType: string;
}

function loadPrefs(): Prefs {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
  } catch {
    return { gitUserName: '', gitUserEmail: '', agentType: 'claude-code' };
  }
}

function savePrefs(p: Partial<Prefs>) {
  const existing = loadPrefs();
  localStorage.setItem(PREFS_KEY, JSON.stringify({ ...existing, ...p }));
}

function AgentCard({ agent, selected, onClick }: { agent: AgentInfo; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`agent-card${selected ? ' agent-card-selected' : ''}`}
      onClick={onClick}
    >
      {agent.icon && <img src={agent.icon} alt="" className="agent-card-icon" />}
      <div className="agent-card-info">
        <span className="agent-card-name">{agent.name}</span>
        <span className="agent-card-desc">{agent.description}</span>
      </div>
    </button>
  );
}

export function SetupForm({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { createSession, phase, error, authUser, autoLaunch, agents, storedKeys } = useSession();
  const didAutoLaunch = useRef(false);
  const [gitUserName, setGitUserName] = useState('');
  const [gitUserEmail, setGitUserEmail] = useState('');
  const [agentType, setAgentType] = useState('claude-code');

  const hasOAuth = authUser && authUser.login !== 'anonymous';
  const selectedAgent = agents.find(a => a.id === agentType);

  // Which keys does this agent need?
  const requiredKeys = selectedAgent?.env_schema.map(e => e.key) || [];
  const configuredKeys = requiredKeys.filter(k => storedKeys.includes(k));

  useEffect(() => {
    const p = loadPrefs();
    if (p.agentType) setAgentType(p.agentType);
    // Pre-fill git identity from OAuth, then override with saved prefs
    if (hasOAuth) {
      if (authUser.name) setGitUserName(authUser.name);
      if (authUser.email) setGitUserEmail(authUser.email);
    }
    if (p.gitUserName) setGitUserName(p.gitUserName);
    if (p.gitUserEmail) setGitUserEmail(p.gitUserEmail);
  }, []);

  // Auto-launch session after OAuth redirect
  useEffect(() => {
    if (autoLaunch && !didAutoLaunch.current && phase === 'idle') {
      didAutoLaunch.current = true;
      const p = loadPrefs();
      createSession({
        gitUserName: p.gitUserName || undefined,
        gitUserEmail: p.gitUserEmail || undefined,
        agentType: p.agentType || undefined,
      });
    }
  }, [autoLaunch, phase, createSession]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    savePrefs({ gitUserName, gitUserEmail, agentType });
    createSession({
      gitUserName: gitUserName || undefined,
      gitUserEmail: gitUserEmail || undefined,
      agentType,
    });
  };

  const isLoading = phase === 'creating';

  return (
    <div className="setup-container">
      <form className="setup-card" onSubmit={handleSubmit}>
        <h1>remolt.dev</h1>
        <p className="subtitle">
          Sandboxed AI coding in your browser.
          {hasOAuth && <span style={{ marginLeft: '0.5rem', opacity: 0.7 }}>Signed in as <strong>{authUser.login}</strong></span>}
        </p>

        {agents.length > 1 && (
          <div className="agent-cards">
            {agents.map(agent => (
              <AgentCard
                key={agent.id}
                agent={agent}
                selected={agentType === agent.id}
                onClick={() => setAgentType(agent.id)}
              />
            ))}
          </div>
        )}

        {requiredKeys.length > 0 && (
          <div className="form-section">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
              <h3 style={{ margin: 0 }}>API Keys</h3>
              <button type="button" className="btn btn-sm btn-ghost" onClick={onOpenSettings}>
                Manage Keys
              </button>
            </div>
            {configuredKeys.length > 0 ? (
              <div className="key-status-list">
                {configuredKeys.map(key => {
                  const label = selectedAgent?.env_schema.find(e => e.key === key)?.label || key;
                  return (
                    <div className="key-status-row" key={key}>
                      <span className="key-status-label">{label}</span>
                      <span className="key-badge key-badge-configured">Ready</span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <span className="form-hint">No keys configured yet. Click Manage Keys to add them.</span>
            )}
          </div>
        )}

        <div className="form-section">
          <h3>Git Identity (optional)</h3>
          <div className="form-group">
            <label>Name</label>
            <input
              type="text"
              value={gitUserName}
              onChange={(e) => setGitUserName(e.target.value)}
              placeholder="Your Name"
            />
          </div>
          <div className="form-group">
            <label>Email</label>
            <input
              type="text"
              value={gitUserEmail}
              onChange={(e) => setGitUserEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
        </div>

        {error && <div className="error-msg">{error}</div>}

        <button type="submit" className="btn btn-primary" disabled={isLoading}>
          {isLoading ? 'Launching...' : 'Launch Session'}
        </button>

        <div style={{ display: 'flex', justifyContent: 'center', gap: '1.5rem', marginTop: '1rem' }}>
          {hasOAuth && (
            <a href="/auth/logout" style={{ fontSize: '0.85rem', opacity: 0.6 }}>Sign out</a>
          )}
          <a className="source-link" href="https://github.com/nthh/remolt.dev" target="_blank" rel="noopener noreferrer">
            Source on GitHub
          </a>
        </div>
      </form>
    </div>
  );
}
