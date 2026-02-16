import { useTerminal } from '../hooks/useTerminal';

interface LogsPanelProps {
  wsUrl: string | null;
  visible: boolean;
}

export function LogsPanel({ wsUrl, visible }: LogsPanelProps) {
  const { containerRef } = useTerminal(wsUrl, { readOnly: true });

  return (
    <div className={`workspace-panel${visible ? '' : ' workspace-panel-hidden'}`}>
      <div className="terminal-body" ref={containerRef} />
    </div>
  );
}
