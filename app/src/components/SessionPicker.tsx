import { useSession } from '../contexts/SessionContext';

function timeAgo(createdAt: number): string {
  const seconds = Math.floor(Date.now() / 1000 - createdAt);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ago`;
}

export function SessionPicker() {
  const { activeSessions, agents, resumeSession, skipResume } = useSession();

  return (
    <div className="setup-container">
      <div className="setup-card">
        <h1>remolt.dev</h1>
        <p className="subtitle">You have active sessions. Resume one or start fresh.</p>
        <div className="session-list">
          {activeSessions.map(s => {
            const agent = agents.find(a => a.id === s.agent_type);
            return (
              <button
                key={s.session_id}
                className="session-card"
                onClick={() => resumeSession(s)}
              >
                {agent?.icon && <img src={agent.icon} alt="" className="agent-card-icon" />}
                <div className="session-card-info">
                  <span className="agent-card-name">{agent?.name ?? s.agent_type}</span>
                  <span className="agent-card-desc">
                    Started {timeAgo((s as any).created_at)}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
        <button className="btn btn-primary" onClick={skipResume}>
          Start New Session
        </button>
      </div>
    </div>
  );
}
