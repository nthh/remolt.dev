import { useState, useEffect, type FormEvent } from 'react';
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
  const { createSession, phase, error } = useSession();
  const [apiKey, setApiKey] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [gitUserName, setGitUserName] = useState('');
  const [gitUserEmail, setGitUserEmail] = useState('');

  useEffect(() => {
    const p = loadPrefs();
    if (p.repoUrl) setRepoUrl(p.repoUrl);
    if (p.gitUserName) setGitUserName(p.gitUserName);
    if (p.gitUserEmail) setGitUserEmail(p.gitUserEmail);
  }, []);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    savePrefs({ repoUrl, gitUserName, gitUserEmail });
    createSession({
      apiKey: apiKey || undefined,
      repoUrl: repoUrl || undefined,
      gitUserName: gitUserName || undefined,
      gitUserEmail: gitUserEmail || undefined,
    });
  };

  const isLoading = phase === 'creating';

  return (
    <div className="setup-container">
      <form className="setup-card" onSubmit={handleSubmit}>
        <h1>Remolt</h1>
        <p className="subtitle">Sandboxed AI coding sessions in your browser.</p>

        <div className="form-group">
          <label>Anthropic API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-ant-... (or leave blank to log in interactively)"
          />
          <span className="form-hint">
            Optional. Without a key, run <code>claude</code> in the terminal and log in via browser.
          </span>
        </div>

        <div className="form-section">
          <h3>GitHub (optional)</h3>
          <div className="form-group">
            <label>Repository URL</label>
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo"
            />
            <span className="form-hint">
              Run <code>gh auth login</code> in the terminal to authenticate with GitHub for private repos.
            </span>
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

        <a className="source-link" href="https://github.com/nthh/remolt.dev" target="_blank" rel="noopener noreferrer">
          Source on GitHub
        </a>
      </form>
    </div>
  );
}
