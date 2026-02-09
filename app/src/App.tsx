import { SessionProvider, useSession } from './contexts/SessionContext';
import { SetupForm } from './components/SetupForm';
import { TerminalView } from './components/TerminalView';

function AppContent() {
  const { phase } = useSession();

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
    return <TerminalView />;
  }

  return <SetupForm />;
}

export function App() {
  return (
    <SessionProvider>
      <AppContent />
    </SessionProvider>
  );
}
