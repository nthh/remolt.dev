interface VSCodePanelProps {
  url: string | null;
  visible: boolean;
}

export function VSCodePanel({ url, visible }: VSCodePanelProps) {
  if (!url) return null;

  return (
    <div className={`workspace-panel${visible ? '' : ' workspace-panel-hidden'}`}>
      <div className="dashboard-frame">
        <iframe src={url} title="VS Code" />
      </div>
    </div>
  );
}
