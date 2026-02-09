import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';

type Phase = 'idle' | 'creating' | 'reconnecting' | 'connected' | 'error';

const SESSION_KEY = 'remolt:session';

interface SessionInfo {
  session_id: string;
  status: string;
  ws_url: string;
}

interface SessionContextType {
  session: SessionInfo | null;
  phase: Phase;
  error: string | null;
  wsUrl: string | null;
  createSession: (params: {
    apiKey?: string;
    githubToken?: string;
    repoUrl?: string;
    gitUserName?: string;
    gitUserEmail?: string;
  }) => Promise<void>;
  destroySession: () => Promise<void>;
}

const SessionContext = createContext<SessionContextType | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);

  const wsUrl = session
    ? `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${session.ws_url}`
    : null;

  // On mount, check if we have a previous session that's still alive
  useEffect(() => {
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) return;

    let info: SessionInfo;
    try {
      info = JSON.parse(saved);
    } catch {
      localStorage.removeItem(SESSION_KEY);
      return;
    }

    setPhase('reconnecting');
    fetch(`/api/sessions/${info.session_id}`)
      .then((res) => {
        if (!res.ok) throw new Error('gone');
        return res.json();
      })
      .then((data: SessionInfo) => {
        setSession(data);
        setPhase('connected');
      })
      .catch(() => {
        localStorage.removeItem(SESSION_KEY);
        setPhase('idle');
      });
  }, []);

  const createSession = useCallback(async (params: {
    apiKey?: string;
    githubToken?: string;
    repoUrl?: string;
    gitUserName?: string;
    gitUserEmail?: string;
  }) => {
    setPhase('creating');
    setError(null);
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: params.apiKey || null,
          github_token: params.githubToken || null,
          repo_url: params.repoUrl || null,
          git_user_name: params.gitUserName || null,
          git_user_email: params.gitUserEmail || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(data.detail || 'Failed to create session');
      }
      const data: SessionInfo = await res.json();
      setSession(data);
      setPhase('connected');
      localStorage.setItem(SESSION_KEY, JSON.stringify(data));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      setPhase('error');
    }
  }, []);

  const destroySession = useCallback(async () => {
    if (!session) return;
    try {
      await fetch(`/api/sessions/${session.session_id}`, { method: 'DELETE' });
    } catch {
      // ignore cleanup errors
    }
    localStorage.removeItem(SESSION_KEY);
    setSession(null);
    setPhase('idle');
    setError(null);
  }, [session]);

  return (
    <SessionContext.Provider value={{ session, phase, error, wsUrl, createSession, destroySession }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within SessionProvider');
  return ctx;
}
