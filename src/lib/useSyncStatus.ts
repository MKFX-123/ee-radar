import { useCallback, useEffect, useRef, useState } from 'react';
import { getSyncStatus } from './api';
import type { SyncJob, SyncStatus } from './api';

export function useSyncStatus(onRevisionChange: () => void, onAvailable: () => void) {
  const [status, setStatus] = useState<SyncStatus | null>(null), [error, setError] = useState('');
  const revision = useRef<string | null>(null);
  const generation = useRef(0);
  const callbacks = useRef({ onRevisionChange, onAvailable });
  callbacks.current = { onRevisionChange, onAvailable };
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    let disposed = false, inFlight = false, unavailable = false, timer: number | undefined;
    let controller: AbortController | null = null;
    const poll = async () => {
      if (disposed || inFlight) return;
      window.clearTimeout(timer); inFlight = true;
      controller = new AbortController();
      const startedGeneration = generation.current;
      let delay = 15000;
      try {
        const next = await getSyncStatus(controller.signal);
        if (disposed) return;
        if (startedGeneration !== generation.current) { delay = 3000; return; }
        setStatus(next); setError(''); callbacks.current.onAvailable();
        const changed = next.data_revision !== null && revision.current !== null && revision.current !== next.data_revision;
        if (changed || unavailable) callbacks.current.onRevisionChange();
        unavailable = false;
        if (next.data_revision !== null) {
          revision.current = next.data_revision;
        }
        delay = next.running ? 3000 : 15000;
      } catch {
        if (!disposed && startedGeneration === generation.current) { unavailable = true; setError('暂时无法读取同步状态，已显示的资讯会保留；连接恢复后自动更新。'); }
      } finally {
        inFlight = false;
        if (!disposed) timer = window.setTimeout(poll, delay);
      }
    };
    const refresh = () => { if (!disposed && !inFlight) { window.clearTimeout(timer); void poll(); } };
    const onVisible = () => { if (document.visibilityState === 'visible') refresh(); };
    refreshRef.current = refresh;
    window.addEventListener('focus', refresh); document.addEventListener('visibilitychange', onVisible);
    void poll();
    return () => {
      disposed = true; window.clearTimeout(timer); controller?.abort(); refreshRef.current = () => {};
      window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  const refresh = useCallback(() => refreshRef.current(), []);
  const acceptJob = useCallback((job: SyncJob) => {
    generation.current += 1;
    setStatus(current => ({
      enabled: current?.enabled ?? false, interval_seconds: current?.interval_seconds ?? 0, next_run_at: current?.next_run_at ?? '',
      data_revision: current?.data_revision ?? null, running: job.status === 'running',
      active_job: job.status === 'running' ? job : null, latest_job: job.status === 'running' ? current?.latest_job ?? null : job,
    }));
    refreshRef.current();
  }, []);
  return { status, error, refresh, acceptJob };
}
