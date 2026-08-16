import React, { useState, useEffect, useRef } from 'react';
import { Activity as ActivityIcon, CheckCircle2, XCircle, Clock, Loader2, RefreshCw, Terminal, StopCircle, Ban } from 'lucide-react';
import { api } from '../services/api';
import { Job } from '../types';

interface ActivityProps {
  activeJobId?: number | null;
}

export const Activity: React.FC<ActivityProps> = ({ activeJobId }) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [liveLogs, setLiveLogs] = useState<string>('');

  const logsContainerRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef<boolean>(true);

  useEffect(() => {
    loadJobs();
  }, []);

  useEffect(() => {
    if (activeJobId) {
      const j = jobs.find(job => job.id === activeJobId);
      if (j) setSelectedJob(j);
    }
  }, [activeJobId, jobs]);

  // Handle user scrolling inside logs container
  const handleLogsScroll = () => {
    if (logsContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = logsContainerRef.current;
      // If user is within 35px of the bottom, keep following output
      const isAtBottom = scrollHeight - scrollTop - clientHeight <= 35;
      shouldStickToBottomRef.current = isAtBottom;
    }
  };

  // Follow output when new logs arrive (if scrolled to bottom)
  useEffect(() => {
    if (shouldStickToBottomRef.current && logsContainerRef.current) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [liveLogs]);

  // Reset to bottom when selecting a different job
  useEffect(() => {
    shouldStickToBottomRef.current = true;
    if (logsContainerRef.current) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [selectedJob?.id]);

  // Connect to SSE for running jobs
  useEffect(() => {
    if (selectedJob && selectedJob.status === 'RUNNING') {
      const evtSource = new EventSource(`/api/v1/jobs/${selectedJob.id}/stream`);
      evtSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setSelectedJob(prev => prev ? { ...prev, status: data.status, progress: data.progress, message: data.message } : null);
          setLiveLogs(prev => prev + data.new_logs);
          if (data.finished) {
            evtSource.close();
            loadJobs();
          }
        } catch (err) {
          console.error(err);
        }
      };
      evtSource.onerror = () => {
        evtSource.close();
      };
      return () => evtSource.close();
    } else if (selectedJob) {
      setLiveLogs(selectedJob.logs || '');
    }
  }, [selectedJob?.id, selectedJob?.status]);

  const loadJobs = async () => {
    setLoading(true);
    try {
      const data = await api.getJobs();
      setJobs(data);
      if (data.length > 0 && !selectedJob) {
        setSelectedJob(data[0]);
      }
    } catch (err) {
      console.error('Failed to load jobs:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Job Queue & Activity</h1>
          <p className="text-xs text-slate-400 mt-1">Real-time status of metadata ingestion, AI episode matching, and consistency audits</p>
        </div>

        <div className="flex items-center space-x-2">
          {jobs.some(j => j.status === 'RUNNING' || j.status === 'PENDING') && (
            <button
              onClick={async () => {
                if (confirm('Are you sure you want to stop all active and pending jobs?')) {
                  try {
                    await api.cancelAllJobs();
                    loadJobs();
                  } catch (err) {
                    console.error('Failed to cancel all jobs:', err);
                  }
                }
              }}
              className="bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition-colors"
            >
              <StopCircle className="w-3.5 h-3.5" />
              <span>Cancel All Jobs</span>
            </button>
          )}

          <button
            onClick={loadJobs}
            className="bg-dark-700 hover:bg-dark-600 text-slate-200 px-4 py-2 rounded-xl text-xs font-semibold border border-dark-600 transition-colors flex items-center space-x-1.5"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Jobs List */}
        <div className="glass-panel rounded-2xl p-4 border border-dark-700 space-y-2 h-[600px] overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-48">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
            </div>
          ) : jobs.length === 0 ? (
            <div className="text-center py-16 text-slate-400 text-xs">
              No recent jobs.
            </div>
          ) : (
            jobs.map(j => {
              const isSelected = selectedJob?.id === j.id;
              return (
                <div
                  key={j.id}
                  onClick={() => {
                    setSelectedJob(j);
                    setLiveLogs(j.logs || '');
                  }}
                  className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-indigo-600/15 border-indigo-500/50 shadow-md'
                      : 'bg-dark-800/60 border-dark-700 hover:bg-dark-700/60'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono text-[11px] font-bold text-slate-300 uppercase">
                      {j.job_type.replace('_', ' ')}
                    </span>
                    <span className="text-[10px] font-mono text-slate-500">#{j.id}</span>
                  </div>

                  <p className="text-xs text-slate-300 line-clamp-1 mb-2">
                    {j.message || 'Processing...'}
                  </p>

                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-1.5 text-[10px] font-semibold">
                      {j.status === 'RUNNING' && (
                        <span className="text-indigo-400 flex items-center space-x-1">
                          <Loader2 className="w-3 h-3 animate-spin" />
                          <span>{j.progress}%</span>
                        </span>
                      )}
                      {j.status === 'COMPLETED' && (
                        <span className="text-emerald-400 flex items-center space-x-1">
                          <CheckCircle2 className="w-3 h-3" />
                          <span>Completed</span>
                        </span>
                      )}
                      {j.status === 'FAILED' && (
                        <span className="text-rose-400 flex items-center space-x-1">
                          <XCircle className="w-3 h-3" />
                          <span>Failed</span>
                        </span>
                      )}
                      {j.status === 'CANCELLED' && (
                        <span className="text-slate-400 flex items-center space-x-1">
                          <Ban className="w-3 h-3" />
                          <span>Cancelled</span>
                        </span>
                      )}
                      {j.status === 'PENDING' && (
                        <span className="text-amber-400 flex items-center space-x-1">
                          <Clock className="w-3 h-3" />
                          <span>Pending</span>
                        </span>
                      )}
                    </div>

                    <span className="text-[10px] text-slate-500">
                      {new Date(j.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Selected Job Detail & Live Console */}
        <div className="lg:col-span-2 glass-panel rounded-2xl p-6 border border-dark-700 flex flex-col h-[600px]">
          {selectedJob ? (
            <>
              <div className="flex items-center justify-between pb-4 border-b border-dark-700 mb-4">
                <div>
                  <div className="flex items-center space-x-2">
                    <h2 className="text-base font-bold text-white uppercase tracking-tight">
                      {selectedJob.job_type.replace('_', ' ')}
                    </h2>
                    <span className="px-2 py-0.5 rounded bg-dark-800 text-slate-400 font-mono text-[10px] border border-dark-700">
                      Job #{selectedJob.id}
                    </span>
                    {selectedJob.status === 'CANCELLED' && (
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-bold text-[10px] border border-slate-700">
                        CANCELLED
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">{selectedJob.message}</p>
                </div>

                <div className="flex items-center space-x-3">
                  {(selectedJob.status === 'RUNNING' || selectedJob.status === 'PENDING') && (
                    <button
                      onClick={async () => {
                        if (confirm(`Are you sure you want to cancel Job #${selectedJob.id}?`)) {
                          try {
                            setSelectedJob(prev => prev ? { ...prev, status: 'CANCELLED', message: 'Job cancelled by user.' } : null);
                            setJobs(prev => prev.map(j => j.id === selectedJob.id ? { ...j, status: 'CANCELLED', message: 'Job cancelled by user.' } : j));
                            await api.cancelJob(selectedJob.id);
                            loadJobs();
                          } catch (err) {
                            console.error('Failed to cancel job:', err);
                            loadJobs();
                          }
                        }
                      }}
                      className="bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition-colors"
                      title="Stop / Cancel Job"
                    >
                      <StopCircle className="w-3.5 h-3.5" />
                      <span>Stop Job</span>
                    </button>
                  )}

                  {(selectedJob.status === 'CANCELLED' || selectedJob.status === 'FAILED') && (
                    <button
                      onClick={async () => {
                        try {
                          await api.restartJob(selectedJob.id);
                          loadJobs();
                        } catch (err: any) {
                          alert(err.message || 'Failed to restart job');
                        }
                      }}
                      className="bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition-colors"
                      title="Restart Job"
                    >
                      <RefreshCw className="w-3.5 h-3.5" />
                      <span>Restart Job</span>
                    </button>
                  )}

                  <div className="text-right">
                    <div className="text-xs font-bold text-indigo-400 font-mono">{selectedJob.progress}%</div>
                    <div className="w-24 h-1.5 rounded-full bg-dark-800 overflow-hidden mt-1">
                      <div
                        className="h-full bg-indigo-500 transition-all duration-300"
                        style={{ width: `${selectedJob.progress}%` }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Console Logs Output */}
              <div
                ref={logsContainerRef}
                onScroll={handleLogsScroll}
                className="flex-1 bg-dark-950 rounded-xl p-4 border border-dark-800 overflow-y-auto font-mono text-xs text-slate-300 leading-relaxed space-y-1"
              >
                <div className="flex items-center space-x-2 text-slate-500 pb-2 border-b border-dark-800 mb-2">
                  <Terminal className="w-3.5 h-3.5" />
                  <span className="text-[11px]">Execution Output</span>
                </div>
                {liveLogs ? (
                  <pre className="whitespace-pre-wrap">{liveLogs}</pre>
                ) : (
                  <p className="text-slate-600 italic">No output recorded yet for this job.</p>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-500">
              <ActivityIcon className="w-8 h-8 mb-2 opacity-50" />
              <p className="text-xs">Select a job from the queue to view logs</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
