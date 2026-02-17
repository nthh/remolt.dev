interface Tab {
  id: string;
  type: string;
  label: string;
  window?: number;
}

interface TabBarProps {
  terminalTabs: Tab[];
  serviceTabs: Tab[];
  activeTabId: string;
  onSelectTab: (id: string) => void;
  onAddTerminal: () => void;
  onCloseTab: (id: string) => void;
  canAddTerminal: boolean;
  onEndSession: () => void;
  onOpenSettings?: () => void;
  onDownload?: () => void;
}

export function TabBar({
  terminalTabs,
  serviceTabs,
  activeTabId,
  onSelectTab,
  onAddTerminal,
  onCloseTab,
  canAddTerminal,
  onEndSession,
  onOpenSettings,
  onDownload,
}: TabBarProps) {
  return (
    <div className="workspace-tabbar">
      <span className="status-dot" />
      <div className="tabbar-group">
        {terminalTabs.map(tab => (
          <button
            key={tab.id}
            className={`tab-btn${activeTabId === tab.id ? ' active' : ''}`}
            onClick={() => onSelectTab(tab.id)}
          >
            <span>{tab.label}</span>
            {terminalTabs.length > 1 && (
              <span
                className="tab-close"
                onClick={(e) => { e.stopPropagation(); onCloseTab(tab.id); }}
              >
                &times;
              </span>
            )}
          </button>
        ))}
        <button
          className="tab-add"
          onClick={onAddTerminal}
          disabled={!canAddTerminal}
          title="New terminal"
        >
          +
        </button>
      </div>
      {serviceTabs.length > 0 && <span className="tabbar-sep" />}
      <div className="tabbar-group">
        {serviceTabs.map(tab => (
          <button
            key={tab.id}
            className={`tab-btn${activeTabId === tab.id ? ' active' : ''}`}
            onClick={() => onSelectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tabbar-end">
        <span className="version-tag">{__APP_VERSION__}</span>
        {onDownload && (
          <button className="btn btn-icon" onClick={onDownload} data-tooltip="Download workspace">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </button>
        )}
        {onOpenSettings && (
          <button className="btn btn-icon" onClick={onOpenSettings} data-tooltip="Settings">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        )}
        <button className="btn btn-danger" onClick={onEndSession}>
          End Session
        </button>
      </div>
    </div>
  );
}
