import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';

type Phase = 'loading' | 'auth_required' | 'idle' | 'creating' | 'reconnecting' | 'connected' | 'error';

const SESSION_KEY = 'remolt:session';

interface SessionInfo {
  session_id: string;
  status: string;
  ws_url: string;
  agent_type: string;
  proxy_url: string | null;
  vscode_url: string | null;
  logs_ws_url: string | null;
}

interface AuthUser {
  login: string;
  name: string;
  email: string;
  auth_required: boolean;
  stored_keys: string[];
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  icon: string;
  has_dashboard: boolean;
  env_schema: { key: string; label: string; secret: boolean; required: boolean }[];
}

interface SessionContextType {
  session: SessionInfo | null;
  phase: Phase;
  error: string | null;
  wsUrl: string | null;
  authUser: AuthUser | null;
  autoLaunch: boolean;
  agents: AgentInfo[];
  storedKeys: string[];
  createSession: (params: {
    gitUserName?: string;
    gitUserEmail?: string;
    agentType?: string;
    agentEnv?: Record<string, string>;
  }) => Promise<void>;
  destroySession: () => Promise<void>;
  updateKeys: (keys: Record<string, string>) => Promise<void>;
  deleteKey: (keyName: string) => Promise<void>;
}

const SessionContext = createContext<SessionContextType | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState<string | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [autoLaunch, setAutoLaunch] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [storedKeys, setStoredKeys] = useState<string[]>([]);

  const wsUrl = session
    ? `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${session.ws_url}`
    : null;

  // On mount: check auth, then check for existing session
  useEffect(() => {
    (async () => {
      // 1. Check auth status
      try {
        const authRes = await fetch('/auth/me');
        if (authRes.status === 401) {
          setPhase('auth_required');
          return;
        }
        if (authRes.ok) {
          const user: AuthUser = await authRes.json();
          setAuthUser(user);
          setStoredKeys(user.stored_keys || []);
        }
      } catch {
        // Server unreachable — proceed without auth (local dev)
      }

      // Fetch available agents
      try {
        const agentsRes = await fetch('/api/agents');
        if (agentsRes.ok) {
          setAgents(await agentsRes.json());
        }
      } catch {
        // Non-fatal
      }

      // 2. Auto-launch after OAuth redirect
      const params = new URLSearchParams(location.search);
      if (params.has('authed')) {
        history.replaceState(null, '', '/');
        setAutoLaunch(true);
        setPhase('idle');
        return;
      }

      // 3. Check for saved session
      const saved = localStorage.getItem(SESSION_KEY);
      if (!saved) {
        setPhase('idle');
        return;
      }

      let info: SessionInfo;
      try {
        info = JSON.parse(saved);
      } catch {
        localStorage.removeItem(SESSION_KEY);
        setPhase('idle');
        return;
      }

      setPhase('reconnecting');
      // Retry a few times — server may be restarting during a deploy
      let recovered = false;
      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          const res = await fetch(`/api/sessions/${info.session_id}`);
          if (res.ok) {
            const data: SessionInfo = await res.json();
            setSession(data);
            setPhase('connected');
            recovered = true;
            break;
          }
          if (res.status === 404) break; // Session genuinely gone
        } catch {
          // Network error — server might be restarting
        }
        await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
      }
      if (!recovered) {
        localStorage.removeItem(SESSION_KEY);
        setPhase('idle');
      }
    })();
  }, []);

  const createSession = useCallback(async (params: {
    gitUserName?: string;
    gitUserEmail?: string;
    agentType?: string;
    agentEnv?: Record<string, string>;
  }) => {
    setPhase('creating');
    setError(null);
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          git_user_name: params.gitUserName || null,
          git_user_email: params.gitUserEmail || null,
          agent_type: params.agentType || null,
          agent_env: params.agentEnv || null,
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
    setAutoLaunch(false);
    setPhase('idle');
    setError(null);
  }, [session]);

  const updateKeys = useCallback(async (keys: Record<string, string>) => {
    const res = await fetch('/api/keys', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys }),
    });
    if (res.ok) {
      const data = await res.json();
      setStoredKeys(data.keys || []);
    }
  }, []);

  const deleteKey = useCallback(async (keyName: string) => {
    const res = await fetch(`/api/keys/${keyName}`, { method: 'DELETE' });
    if (res.ok) {
      const data = await res.json();
      setStoredKeys(data.keys || []);
    }
  }, []);

  return (
    <SessionContext.Provider value={{ session, phase, error, wsUrl, authUser, autoLaunch, agents, storedKeys, createSession, destroySession, updateKeys, deleteKey }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within SessionProvider');
  return ctx;
}
