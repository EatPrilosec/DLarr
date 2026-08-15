import React, { useState, useEffect } from 'react';
import { Tv, Sparkles, Layers, ArrowRight, RefreshCw, Trash2, CheckCircle2, AlertTriangle, ShieldCheck } from 'lucide-react';
import { api } from '../services/api';
import { Show } from '../types';
import { StatusBadge } from '../components/StatusBadge';

interface DashboardProps {
  onSelectShow: (showId: number) => void;
  onOpenImport: () => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onSelectShow, onOpenImport }) => {
  const [shows, setShows] = useState<Show[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  useEffect(() => {
    loadShows();
  }, []);

  const loadShows = async () => {
    setLoading(true);
    try {
      const data = await api.getShows();
      setShows(data);
    } catch (err) {
      console.error('Failed to load shows:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, showId: number) => {
    e.stopPropagation();
    if (!confirm('Are you sure you want to remove this show and its multi-source database?')) return;
    setDeletingId(showId);
    try {
      await api.deleteShow(showId);
      setShows(shows.filter(s => s.id !== showId));
    } catch (err) {
      alert('Failed to delete show');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 animate-fade-in">
      {/* Header Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-indigo-900/60 via-dark-800 to-dark-800 border border-indigo-500/20 p-8 mb-8 shadow-xl">
        <div className="relative z-10 max-w-2xl">
          <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-indigo-500/20 border border-indigo-500/30 text-indigo-300 text-xs font-semibold mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            <span>AI-Enhanced Multi-Source Database</span>
          </div>
          <h1 className="text-3xl font-extrabold text-white tracking-tight">
            Episode Matching & Variation Hub
          </h1>
          <p className="text-slate-300 mt-2 text-sm leading-relaxed">
            DLarr crawls Sonarr, TMDB, TVmaze, OMDb, and SubDL transcripts to build an exhaustive episode variation database. Ollama AI confirms non-standard numberings, title shifts, and audits consistency.
          </p>
          <div className="mt-6 flex items-center space-x-3">
            <button
              onClick={onOpenImport}
              className="bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105"
            >
              Import From Sonarr
            </button>
            <button
              onClick={loadShows}
              className="bg-dark-700/80 hover:bg-dark-700 text-slate-200 px-4 py-2.5 rounded-xl text-sm font-semibold border border-dark-600 transition-colors flex items-center space-x-2"
            >
              <RefreshCw className="w-4 h-4" />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Decorative background glow */}
        <div className="absolute right-0 top-0 bottom-0 w-1/2 bg-gradient-to-l from-indigo-600/10 to-transparent pointer-events-none" />
      </div>

      {/* Shows Grid */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">Indexed Shows ({shows.length})</h2>
          <p className="text-xs text-slate-400">Click any series to open the multi-source episode inspector</p>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map(n => (
            <div key={n} className="h-64 rounded-2xl bg-dark-800/50 border border-dark-700/50 animate-pulse" />
          ))}
        </div>
      ) : shows.length === 0 ? (
        <div className="text-center py-20 rounded-2xl bg-dark-800/30 border border-dashed border-dark-700">
          <Tv className="w-12 h-12 mx-auto text-slate-600 mb-3" />
          <h3 className="text-base font-bold text-white">No shows indexed yet</h3>
          <p className="text-xs text-slate-400 max-w-sm mx-auto mt-1 mb-6">
            Configure your connections in Settings and import your first show from Sonarr to start building the AI episode database.
          </p>
          <button
            onClick={onOpenImport}
            className="bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold shadow-lg shadow-indigo-600/25 transition-all"
          >
            Import First Show
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {shows.map(show => (
            <div
              key={show.id}
              onClick={() => onSelectShow(show.id)}
              className="glass-card rounded-2xl overflow-hidden cursor-pointer transition-all duration-300 hover:-translate-y-1 flex flex-col justify-between group"
            >
              <div>
                <div className="relative h-48 bg-dark-900 overflow-hidden">
                  {show.poster_url ? (
                    <img
                      src={show.poster_url}
                      alt={show.title}
                      className="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center bg-dark-800 text-slate-600">
                      <Tv className="w-12 h-12" />
                    </div>
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-dark-900 via-dark-900/40 to-transparent" />
                  
                  {/* Top Badges */}
                  <div className="absolute top-3 right-3 flex items-center space-x-1.5">
                    <StatusBadge status={show.audit_status} />
                  </div>

                  {/* Title Overlay */}
                  <div className="absolute bottom-3 left-4 right-4">
                    <h3 className="text-lg font-bold text-white leading-tight group-hover:text-indigo-300 transition-colors">
                      {show.title}
                    </h3>
                    <div className="flex items-center space-x-2 text-xs text-slate-400 mt-0.5">
                      {show.year && <span>{show.year}</span>}
                      <span>•</span>
                      <span>{show.episode_count} Canonical Episodes</span>
                    </div>
                  </div>
                </div>

                {/* Body details */}
                <div className="p-4 space-y-3">
                  <p className="text-xs text-slate-400 line-clamp-2">
                    {show.overview || 'No overview available.'}
                  </p>

                  {/* Sources mapped pill list */}
                  <div className="pt-2 border-t border-dark-700/60">
                    <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                      Mapped Sources
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {show.mapped_sources_summary?.sources.map(src => (
                        <span
                          key={src}
                          className="px-2 py-0.5 rounded bg-dark-700 text-slate-300 border border-dark-600 text-[10px] font-mono font-medium uppercase"
                        >
                          {src}
                        </span>
                      )) || <span className="text-xs text-slate-500">None</span>}
                    </div>
                  </div>
                </div>
              </div>

              {/* Card Footer */}
              <div className="px-4 py-3 bg-dark-800/80 border-t border-dark-700/60 flex items-center justify-between">
                <button
                  onClick={(e) => handleDelete(e, show.id)}
                  disabled={deletingId === show.id}
                  className="p-1.5 text-slate-500 hover:text-rose-400 rounded-lg hover:bg-dark-700 transition-colors"
                  title="Delete Show"
                >
                  <Trash2 className="w-4 h-4" />
                </button>

                <div className="flex items-center space-x-1 text-xs font-semibold text-indigo-400 group-hover:text-indigo-300 transition-colors">
                  <span>Inspect Episodes</span>
                  <ArrowRight className="w-4 h-4 transform group-hover:translate-x-0.5 transition-transform" />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
