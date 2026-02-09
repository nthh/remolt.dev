import { useState, useEffect, useRef, type FormEvent } from 'react';
import { useSession } from '../contexts/SessionContext';

const PREFS_KEY = 'remolt:prefs';

interface Prefs {
  repoUrl: string;
  gitUserName: string;
  gitUserEmail: string;
}

function loadPrefs(): Prefs {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
  } catch {
    return { repoUrl: '', gitUserName: '', gitUserEmail: '' };
  }
}

function savePrefs(p: Partial<Prefs>) {
  const existing = loadPrefs();
  localStorage.setItem(PREFS_KEY, JSON.stringify({ ...existing, ...p }));
}

export function SetupForm() {
  const { createSession, phase, error, authUser, autoLaunch } = useSession();
  const didAutoLaunch = useRef(false);
  const [repoUrl, setRepoUrl] = useState('');
  const [gitUserName, setGitUserName] = useState('');
  const [gitUserEmail, setGitUserEmail] = useState('');

  const hasOAuth = authUser && authUser.login !== 'anonymous';

  useEffect(() => {
    const p = loadPrefs();
    if (p.repoUrl) setRepoUrl(p.repoUrl);
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
      });
    }
  }, [autoLaunch, phase, createSession]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    savePrefs({ repoUrl, gitUserName, gitUserEmail });
    createSession({
      repoUrl: repoUrl || undefined,
      gitUserName: gitUserName || undefined,
      gitUserEmail: gitUserEmail || undefined,
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

        <div className="form-section">
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
