import { useEffect, useRef, useCallback, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';

const MAX_RECONNECT_ATTEMPTS = 15;
const RECONNECT_BASE_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 5000;
const URL_REGEX = /https?:\/\/[^\s\x1b\x00-\x1f]{20,}/g;

export function useTerminal(wsUrl: string | null) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);

  // Buffer for detecting URLs that arrive across multiple WS frames
  const bufferRef = useRef('');
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const checkForUrls = useCallback((text: string) => {
    bufferRef.current += text;

    // Debounce — wait for output to settle before scanning
    if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
    flushTimerRef.current = setTimeout(() => {
      const buf = bufferRef.current;
      // Strip ANSI escape sequences for cleaner matching
      const clean = buf.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '');
      const matches = clean.match(URL_REGEX);
      if (matches) {
        for (const url of matches) {
          if (url.includes('oauth') || url.includes('authorize') || url.includes('login')) {
            setAuthUrl(url);
            // Auto-open in new tab
            window.open(url, '_blank', 'noopener,noreferrer');
            break;
          }
        }
      }
      // Keep only last 2KB to avoid unbounded growth
      if (bufferRef.current.length > 2048) {
        bufferRef.current = bufferRef.current.slice(-1024);
      }
    }, 200);
  }, []);

  const dismissAuth = useCallback(() => setAuthUrl(null), []);

  useEffect(() => {
    if (!containerRef.current || !wsUrl) return;

    const url = wsUrl;
    bufferRef.current = '';
    setAuthUrl(null);

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      scrollback: 10000,
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      theme: {
        background: '#1a1b26',
        foreground: '#a9b1d6',
        cursor: '#c0caf5',
        selectionBackground: '#33467c',
        black: '#15161e',
        red: '#f7768e',
        green: '#9ece6a',
        yellow: '#e0af68',
        blue: '#7aa2f7',
        magenta: '#bb9af7',
        cyan: '#7dcfff',
        white: '#a9b1d6',
        brightBlack: '#414868',
        brightRed: '#f7768e',
        brightGreen: '#9ece6a',
        brightYellow: '#e0af68',
        brightBlue: '#7aa2f7',
        brightMagenta: '#bb9af7',
        brightCyan: '#7dcfff',
        brightWhite: '#c0caf5',
      },
    });

    const fit = new FitAddon();
    const webLinks = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(webLinks);
    // Let Ctrl+V / Cmd+V fall through to browser paste handling
    term.attachCustomKeyEventHandler((ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'v') return false;
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'c' && term.hasSelection()) return false;
      return true;
    });

    term.open(containerRef.current);
    fit.fit();
    term.focus();
    termRef.current = term;

    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let ws: WebSocket | null = null;
    let attempts = 0;
    let intentionalClose = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let everConnected = false;

    function connect() {
      ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = () => {
        const wasReconnect = everConnected;
        everConnected = true;
        attempts = 0;
        term.writeln(wasReconnect
          ? '\x1b[32m● Reconnected\x1b[0m\r'
          : '\x1b[32m● Connected\x1b[0m\r');
        term.focus();
        const dims = fit.proposeDimensions();
        if (dims) {
          ws!.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
        }
      };

      ws.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
          const bytes = new Uint8Array(ev.data);
          term.write(bytes);
          checkForUrls(decoder.decode(bytes, { stream: true }));
        }
      };

      ws.onclose = () => {
        if (intentionalClose) return;
        if (attempts < MAX_RECONNECT_ATTEMPTS) {
          // Short fixed delay while waiting for sandbox, exponential backoff after connected
          const delay = everConnected
            ? Math.min(RECONNECT_BASE_MS * Math.pow(2, attempts), MAX_RECONNECT_DELAY_MS)
            : Math.min(2000, RECONNECT_BASE_MS * (attempts + 1));
          if (everConnected) {
            term.writeln(`\r\n\x1b[33m● Connection lost. Reconnecting in ${delay / 1000}s...\x1b[0m`);
          } else {
            term.writeln(`\x1b[33m● Waiting for sandbox... (${attempts + 1}/${MAX_RECONNECT_ATTEMPTS})\x1b[0m\r`);
          }
          attempts++;
          reconnectTimer = setTimeout(connect, delay);
        } else {
          term.writeln('\r\n\x1b[31m● Disconnected. Refresh to retry.\x1b[0m');
        }
      };

      ws.onerror = () => {};
    }

    connect();

    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(encoder.encode(data));
      }
    });

    // Click anywhere on the terminal container to focus (helps with padding areas)
    const el = containerRef.current;
    const handleClick = () => term.focus();
    el.addEventListener('click', handleClick);

    const refit = () => {
      fit.fit();
      const dims = fit.proposeDimensions();
      if (dims && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
      }
    };

    const ro = new ResizeObserver(refit);
    ro.observe(el);

    // Mobile: resize terminal container to fit above virtual keyboard
    const vv = window.visualViewport;
    const handleViewport = () => {
      if (!vv) return;
      const container = el.closest('.terminal-container') as HTMLElement;
      if (container) {
        container.style.height = `${vv.height}px`;
      }
      refit();
    };
    vv?.addEventListener('resize', handleViewport);

    return () => {
      intentionalClose = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
      el.removeEventListener('click', handleClick);
      ro.disconnect();
      vv?.removeEventListener('resize', handleViewport);
      if (ws) ws.close();
      wsRef.current = null;
      termRef.current = null;
      term.dispose();
    };
  }, [wsUrl, checkForUrls]);

  const sendText = useCallback((text: string) => {
    if (text && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(new TextEncoder().encode(text));
    }
    termRef.current?.focus();
  }, []);

  const scrollUp = useCallback(() => {
    termRef.current?.scrollPages(-1);
  }, []);

  const scrollDown = useCallback(() => {
    termRef.current?.scrollPages(1);
  }, []);

  return { containerRef, authUrl, dismissAuth, sendText, scrollUp, scrollDown };
}
