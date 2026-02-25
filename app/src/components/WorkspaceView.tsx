import { useState, useCallback, useMemo } from 'react';
import { useSession } from '../contexts/SessionContext';
import { TabBar } from './TabBar';
import { TerminalPanel } from './TerminalPanel';
import { DashboardPanel } from './DashboardPanel';
import { LogsPanel } from './LogsPanel';
import { VSCodePanel } from './VSCodePanel';

interface Tab {
  id: string;
  type: 'terminal' | 'logs' | 'dashboard' | 'vscode';
  label: string;
  window?: number;
}

const MAX_TERMINALS = 5;

export function WorkspaceView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { session, wsUrl, destroySession } = useSession();
  const hasDashboard = !!session?.proxy_url;

  const initialTerminals = session?.terminals ?? 1;
  const [terminalTabs, setTerminalTabs] = useState<Tab[]>(() =>
    Array.from({ length: initialTerminals }, (_, i) => ({
      id: `term-${i}`,
      type: 'terminal' as const,
      label: `Term ${i + 1}`,
      window: i,
    }))
  );
  const [activeTabId, setActiveTabId] = useState('term-0');
  const [nextTermNum, setNextTermNum] = useState(initialTerminals + 1);
  const [nextWindow, setNextWindow] = useState(initialTerminals);

  const addTerminal = useCallback(() => {
    if (terminalTabs.length >= MAX_TERMINALS) return;
    const id = `term-${nextWindow}`;
    const tab: Tab = { id, type: 'terminal', label: `Term ${nextTermNum}`, window: nextWindow };
    setTerminalTabs(prev => [...prev, tab]);
    setActiveTabId(id);
    setNextTermNum(n => n + 1);
    setNextWindow(w => w + 1);
  }, [terminalTabs.length, nextTermNum, nextWindow]);

  const closeTab = useCallback((tabId: string) => {
    setTerminalTabs(prev => {
      if (prev.length <= 1) return prev;
      const idx = prev.findIndex(t => t.id === tabId);
      if (idx === -1) return prev;
      const next = prev.filter(t => t.id !== tabId);
      if (activeTabId === tabId) {
        const newIdx = Math.min(idx, next.length - 1);
        setActiveTabId(next[newIdx].id);
      }
      return next;
    });
  }, [activeTabId]);

  const terminalWsUrl = useCallback((window: number) => {
    if (!wsUrl) return null;
    return window === 0 ? wsUrl : `${wsUrl}?window=${window}`;
  }, [wsUrl]);

  const logsWsUrl = useMemo(() => {
    if (!session || !hasDashboard) return null;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/ws/logs/${session.session_id}`;
  }, [session, hasDashboard]);

  const vscodeUrl = useMemo(() => {
    if (!session) return null;
    return `/vscode/${session.session_id}/`;
  }, [session]);

  const downloadWorkspace = useCallback(async () => {
    if (!session) return;
    try {
      const resp = await fetch(`/api/sessions/${session.session_id}/download`, {
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      a.download = `remolt-workspace-${ts}.tar.gz`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Download failed:', err);
    }
  }, [session]);

  const [uploading, setUploading] = useState(false);

  const uploadWorkspace = useCallback(() => {
    if (!session || uploading) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.tar.gz,.tgz,application/gzip,application/x-gzip';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setUploading(true);
      try {
        const resp = await fetch(`/api/sessions/${session.session_id}/upload`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/gzip' },
          body: file,
        });
        if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`);
      } catch (err) {
        console.error('Upload failed:', err);
      } finally {
        setUploading(false);
      }
    };
    input.click();
  }, [session, uploading]);

  const serviceTabs: Tab[] = useMemo(() => {
    const tabs: Tab[] = [];
    if (hasDashboard) {
      tabs.push({ id: 'logs', type: 'logs', label: 'Logs' });
      tabs.push({ id: 'dashboard', type: 'dashboard', label: 'Dashboard' });
    }
    tabs.push({ id: 'vscode', type: 'vscode', label: 'VS Code' });
    return tabs;
  }, [hasDashboard]);

  return (
    <div className="terminal-container">
      <TabBar
        terminalTabs={terminalTabs}
        serviceTabs={serviceTabs}
        activeTabId={activeTabId}
        onSelectTab={setActiveTabId}
        onAddTerminal={addTerminal}
        onCloseTab={closeTab}
        canAddTerminal={terminalTabs.length < MAX_TERMINALS}
        onEndSession={destroySession}
        onOpenSettings={onOpenSettings}
        onDownload={downloadWorkspace}
        onUpload={uploading ? undefined : uploadWorkspace}
      />
      <div className="workspace-content">
        {terminalTabs.map(tab => (
          <TerminalPanel
            key={tab.id}
            wsUrl={terminalWsUrl(tab.window!)}
            visible={activeTabId === tab.id}
          />
        ))}
        {hasDashboard && (
          <>
            <LogsPanel
              wsUrl={logsWsUrl}
              visible={activeTabId === 'logs'}
            />
            <DashboardPanel
              proxyUrl={session!.proxy_url!}
              visible={activeTabId === 'dashboard'}
            />
          </>
        )}
        <VSCodePanel
          url={vscodeUrl}
          visible={activeTabId === 'vscode'}
        />
      </div>
    </div>
  );
}
