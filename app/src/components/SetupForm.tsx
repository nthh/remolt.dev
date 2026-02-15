import { useState, useEffect, useRef, type FormEvent } from 'react';
import { useSession, type AgentInfo } from '../contexts/SessionContext';

const PREFS_KEY = 'remolt:prefs';

interface Prefs {
  repoUrl: string;
  gitUserName: string;
  gitUserEmail: string;
  agentType: string;
}

function loadPrefs(): Prefs {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
  } catch {
    return { repoUrl: '', gitUserName: '', gitUserEmail: '', agentType: 'claude-code' };
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

export function SetupForm() {
  const { createSession, phase, error, authUser, autoLaunch, agents } = useSession();
  const didAutoLaunch = useRef(false);
  const [repoUrl, setRepoUrl] = useState('');
  const [gitUserName, setGitUserName] = useState('');
  const [gitUserEmail, setGitUserEmail] = useState('');
  const [agentType, setAgentType] = useState('claude-code');
  const [agentEnv, setAgentEnv] = useState<Record<string, string>>({});

  const hasOAuth = authUser && authUser.login !== 'anonymous';
  const selectedAgent = agents.find(a => a.id === agentType);

  useEffect(() => {
    const p = loadPrefs();
    if (p.repoUrl) setRepoUrl(p.repoUrl);
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
        repoUrl: p.repoUrl || undefined,
        gitUserName: p.gitUserName || undefined,
        gitUserEmail: p.gitUserEmail || undefined,
        agentType: p.agentType || undefined,
      });
    }
  }, [autoLaunch, phase, createSession]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    savePrefs({ repoUrl, gitUserName, gitUserEmail, agentType });
    createSession({
      repoUrl: repoUrl || undefined,
      gitUserName: gitUserName || undefined,
      gitUserEmail: gitUserEmail || undefined,
      agentType,
      agentEnv: Object.keys(agentEnv).length > 0 ? agentEnv : undefined,
    });
  };

  const isLoading = phase === 'creating';

  return (
    <div className="setup-container">
      <form className="setup-card" onSubmit={handleSubmit}>
        <div className="form-header">
          <h1>remolt.dev</h1>
          <p className="subtitle">
            Sandboxed AI coding in your browser.
            {hasOAuth && <span style={{ marginLeft: '0.5rem', opacity: 0.7 }}>Signed in as <strong>{authUser.login}</strong></span>}
          </p>
        </div>

        {agents.length > 1 && (
          <div className="agent-cards">
            {agents.map(agent => (
              <AgentCard
                key={agent.id}
                agent={agent}
                selected={agentType === agent.id}
                onClick={() => {
                  setAgentType(agent.id);
                  setAgentEnv({});
                }}
              />
            ))}
          </div>
        )}

        <div className="form-section form-section-repo">
          <h3>Repository (optional)</h3>
          <div className="form-group">
            <label>Repository URL</label>
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo"
            />
            {hasOAuth ? (
              <span className="form-hint">
                Your GitHub token is available in the sandbox for private repo access.
              </span>
            ) : (
              <span className="form-hint">
                Run <code>gh auth login</code> in the terminal to authenticate with GitHub for private repos.
              </span>
            )}
          </div>
        </div>

        {selectedAgent && selectedAgent.env_schema.length > 0 && (
          <div className="form-section form-section-api">
            <h3>API Keys (optional)</h3>
            {selectedAgent.env_schema.map(field => (
              <div className="form-group" key={field.key}>
                <label>{field.label}</label>
                <input
                  type={field.secret ? 'password' : 'text'}
                  value={agentEnv[field.key] || ''}
                  onChange={(e) => setAgentEnv(prev => ({ ...prev, [field.key]: e.target.value }))}
                  placeholder={field.label}
                  autoComplete="off"
                />
              </div>
            ))}
          </div>
        )}

        <div className="form-section form-section-git">
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

        {error && <div className="error-msg form-error">{error}</div>}

        <button type="submit" className="btn btn-primary form-submit-btn" disabled={isLoading}>
          {isLoading ? 'Launching...' : 'Launch Session'}
        </button>

        <div className="form-footer">
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
