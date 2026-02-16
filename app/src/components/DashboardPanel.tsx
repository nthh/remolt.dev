interface DashboardPanelProps {
  proxyUrl: string;
  visible: boolean;
}

export function DashboardPanel({ proxyUrl, visible }: DashboardPanelProps) {
  return (
    <div className={`workspace-panel${visible ? '' : ' workspace-panel-hidden'}`}>
      <div className="dashboard-frame">
        <iframe src={proxyUrl} title="Agent Dashboard" />
      </div>
    </div>
  );
}
