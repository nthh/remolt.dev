import { useState } from 'react';
import { SessionProvider, useSession } from './contexts/SessionContext';
import { SetupForm } from './components/SetupForm';
import { WorkspaceView } from './components/WorkspaceView';
import { SettingsModal } from './components/SettingsModal';

function LoginScreen() {
  const [repoScope, setRepoScope] = useState<'none' | 'public' | 'private'>('none');
  const [emailScope, setEmailScope] = useState(false);

  const scopes: string[] = [];
  if (repoScope === 'public') scopes.push('public_repo');
  if (repoScope === 'private') scopes.push('repo');
  if (emailScope) scopes.push('user:email');
  const loginUrl = scopes.length > 0 ? `/auth/login?scopes=${scopes.join(',')}` : '/auth/login';

  return (
    <div className="setup-container">
      <div className="setup-card" style={{ textAlign: 'center' }}>
        <h1>remolt.dev</h1>
        <p className="subtitle">Sandboxed AI coding in your browser.</p>
        <p style={{ fontSize: '0.85rem', opacity: 0.7, margin: '1rem 0 0.75rem' }}>
          Sign in with GitHub to verify your identity. No permissions are required unless you opt in below.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', margin: '0 auto 0.75rem', maxWidth: '20rem', textAlign: 'left' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem' }}>
            <input type="radio" name="repo-scope" checked={repoScope === 'none'} onChange={() => setRepoScope('none')} />
            No repository access
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem' }}>
            <input type="radio" name="repo-scope" checked={repoScope === 'public'} onChange={() => setRepoScope('public')} />
            Push to public repositories
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem' }}>
            <input type="radio" name="repo-scope" checked={repoScope === 'private'} onChange={() => setRepoScope('private')} />
            Access private repositories
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.9rem', marginTop: '0.25rem' }}>
            <input type="checkbox" checked={emailScope} onChange={(e) => setEmailScope(e.target.checked)} />
            Share your email for git commits
          </label>
        </div>
        <span className="form-hint" style={{ display: 'block', marginBottom: '1rem' }}>
          Optional. You can also run <code>gh auth login</code> in the terminal later.
        </span>
        <a href={loginUrl} className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}>
          <svg width="20" height="20" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
          Sign in with GitHub
        </a>
        <p style={{ fontSize: '0.8rem', opacity: 0.5, marginTop: '1.5rem', lineHeight: 1.5 }}>
          Credentials live only in your sandbox and are destroyed when the session ends.<br />
          Nothing is stored on our servers.
        </p>
        <a className="source-link" href="https://github.com/nthh/remolt.dev" target="_blank" rel="noopener noreferrer">
          Source on GitHub
        </a>
      </div>
    </div>
  );
}

function AppContent() {
  const { phase } = useSession();
  const [showSettings, setShowSettings] = useState(false);

  if (phase === 'loading') {
    return (
      <div className="setup-container">
        <div className="setup-card" style={{ textAlign: 'center' }}>
          <p className="subtitle">Loading...</p>
        </div>
      </div>
    );
  }

  if (phase === 'auth_required') {
    return <LoginScreen />;
  }

  if (phase === 'reconnecting') {
    return (
      <div className="setup-container">
        <div className="setup-card" style={{ textAlign: 'center' }}>
          <h1>Reconnecting...</h1>
          <p className="subtitle">Restoring your previous session.</p>
        </div>
      </div>
    );
  }

  if (phase === 'connected') {
    return (
      <>
        <WorkspaceView onOpenSettings={() => setShowSettings(true)} />
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </>
    );
  }

  return (
    <>
      <button className="settings-gear-fixed" onClick={() => setShowSettings(true)} title="Settings">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
      <SetupForm onOpenSettings={() => setShowSettings(true)} />
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </>
  );
}

export function App() {
  return (
    <SessionProvider>
      <AppContent />
    </SessionProvider>
  );
}
