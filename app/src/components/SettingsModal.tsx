import { useState } from 'react';
import { useSession } from '../contexts/SessionContext';

const PROVIDERS = [
  { key: 'ANTHROPIC_API_KEY', label: 'Anthropic' },
  { key: 'OPENAI_API_KEY', label: 'OpenAI' },
  { key: 'GEMINI_API_KEY', label: 'Gemini' },
  { key: 'OPENROUTER_API_KEY', label: 'OpenRouter' },
];

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const { storedKeys, updateKeys, deleteKey } = useSession();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [copyOnSelect, setCopyOnSelect] = useState(() => {
    const stored = localStorage.getItem('remolt:copyOnSelect');
    return stored === null ? true : stored === 'true';
  });

  const handleSave = async (keyName: string) => {
    if (!inputValue.trim()) return;
    setSaving(true);
    await updateKeys({ [keyName]: inputValue.trim() });
    setSaving(false);
    setEditingKey(null);
    setInputValue('');
  };

  const handleRemove = async (keyName: string) => {
    setSaving(true);
    await deleteKey(keyName);
    setSaving(false);
    if (editingKey === keyName) {
      setEditingKey(null);
      setInputValue('');
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          <h3 className="settings-section-title">API Keys</h3>
          <p className="settings-section-desc">
            Keys are stored in an encrypted cookie and auto-injected into sessions.
          </p>
          {PROVIDERS.map(({ key, label }) => {
            const isConfigured = storedKeys.includes(key);
            const isEditing = editingKey === key;
            return (
              <div className="key-row" key={key}>
                <div className="key-row-info">
                  <span className="key-row-name">{label}</span>
                  {isConfigured ? (
                    <span className="key-badge key-badge-configured">Configured</span>
                  ) : (
                    <span className="key-badge key-badge-missing">Not set</span>
                  )}
                </div>
                {isEditing ? (
                  <div className="key-row-edit">
                    <input
                      type="password"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder={`Enter ${label} API key`}
                      autoFocus
                      autoComplete="off"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSave(key);
                        if (e.key === 'Escape') { setEditingKey(null); setInputValue(''); }
                      }}
                    />
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={saving || !inputValue.trim()}
                      onClick={() => handleSave(key)}
                    >
                      Save
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => { setEditingKey(null); setInputValue(''); }}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="key-row-actions">
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => { setEditingKey(key); setInputValue(''); }}
                    >
                      {isConfigured ? 'Update' : 'Add'}
                    </button>
                    {isConfigured && (
                      <button
                        className="btn btn-sm btn-ghost btn-ghost-danger"
                        disabled={saving}
                        onClick={() => handleRemove(key)}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          <div className="settings-divider" />

          <h3 className="settings-section-title">Terminal</h3>
          <div className="toggle-row">
            <span className="toggle-row-label">Copy on select</span>
            <button
              className="toggle-switch"
              role="switch"
              aria-checked={copyOnSelect}
              onClick={() => {
                const next = !copyOnSelect;
                setCopyOnSelect(next);
                localStorage.setItem('remolt:copyOnSelect', String(next));
                window.dispatchEvent(new CustomEvent('remolt:settingsChanged'));
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
