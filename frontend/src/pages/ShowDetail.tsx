import React, { useState, useEffect } from 'react';
import { ArrowLeft, Sparkles, ShieldCheck, RefreshCw, FileText, ChevronDown, ChevronRight, Layers, Check, AlertCircle } from 'lucide-react';
import { api } from '../services/api';
import { Show, Episode, EpisodeSourceMetadata } from '../types';
import { StatusBadge } from '../components/StatusBadge';

interface ShowDetailProps {
  showId: number;
  onBack: () => void;
  onJobStarted: (jobId: number) => void;
}

export const ShowDetail: React.FC<ShowDetailProps> = ({ showId, onBack, onJobStarted }) => {
  const [show, setShow] = useState<Show | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null);
  const [expandedEpisodeId, setExpandedEpisodeId] = useState<number | null>(null);
  const [transcriptModal, setTranscriptModal] = useState<{ title: string; content: string } | null>(null);
  const [auditing, setAuditing] = useState(false);

  useEffect(() => {
    loadShowDetail();
  }, [showId]);

  const loadShowDetail = async () => {
    setLoading(true);
    try {
      const data = await api.getShow(showId);
      setShow(data);
      if (data.episodes && data.episodes.length > 0 && selectedSeason === null) {
        // Default to first season in list
        setSelectedSeason(data.episodes[0].season_number);
      }
    } catch (err) {
      console.error('Failed to load show detail:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleTriggerAudit = async () => {
    if (!show) return;
    setAuditing(true);
    try {
      const res = await api.triggerAudit(show.id);
      onJobStarted(res.job_id);
    } catch (err) {
      alert('Failed to start AI audit');
    } finally {
      setAuditing(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto px-6 py-12">
        <div className="h-64 rounded-2xl bg-dark-800/50 border border-dark-700/50 animate-pulse mb-8" />
        <div className="space-y-4">
          {[1, 2, 3, 4].map(n => (
            <div key={n} className="h-16 rounded-xl bg-dark-800/40 border border-dark-700/40 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!show) {
    return (
      <div className="max-w-7xl mx-auto px-6 py-12 text-center">
        <p className="text-slate-400">Show not found.</p>
        <button onClick={onBack} className="mt-4 px-4 py-2 bg-indigo-600 text-white rounded-xl text-xs font-semibold">
          Back to Library
        </button>
      </div>
    );
  }

  // Get distinct seasons
  const seasons = Array.from(new Set(show.episodes?.map(e => e.season_number) || [])).sort((a, b) => a - b);
  const currentEpisodes = show.episodes?.filter(e => e.season_number === selectedSeason) || [];

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 animate-fade-in">
      {/* Top Navigation */}
      <button
        onClick={onBack}
        className="flex items-center space-x-2 text-slate-400 hover:text-white transition-colors mb-6 text-sm font-medium"
      >
        <ArrowLeft className="w-4 h-4" />
        <span>Back to Library</span>
      </button>

      {/* Show Hero Banner */}
      <div className="glass-panel rounded-2xl p-6 sm:p-8 mb-8 border border-dark-700 shadow-2xl flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="flex items-start sm:items-center space-x-6">
          {show.poster_url ? (
            <img
              src={show.poster_url}
              alt={show.title}
              className="w-24 h-36 object-cover rounded-xl shadow-lg border border-dark-600 flex-shrink-0"
            />
          ) : (
            <div className="w-24 h-36 rounded-xl bg-dark-800 border border-dark-600 flex items-center justify-center text-slate-600 flex-shrink-0" />
          )}

          <div>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <h1 className="text-2xl sm:text-3xl font-extrabold text-white">{show.title}</h1>
              {show.year && <span className="text-base text-slate-400">({show.year})</span>}
              <StatusBadge status={show.audit_status} />
            </div>

            <p className="text-xs sm:text-sm text-slate-300 max-w-3xl leading-relaxed line-clamp-2">
              {show.overview || 'No show overview.'}
            </p>

            <div className="flex flex-wrap items-center gap-4 mt-3 text-xs text-slate-400 font-mono">
              {show.tvdb_id && <span>TVDB: <strong className="text-slate-200">{show.tvdb_id}</strong></span>}
              {show.tmdb_id && <span>TMDB: <strong className="text-slate-200">{show.tmdb_id}</strong></span>}
              {show.imdb_id && <span>IMDb: <strong className="text-slate-200">{show.imdb_id}</strong></span>}
              {show.tvmaze_id && <span>TVmaze: <strong className="text-slate-200">{show.tvmaze_id}</strong></span>}
            </div>
          </div>
        </div>

        {/* Action button */}
        <div className="flex-shrink-0 flex items-center space-x-3">
          <button
            onClick={handleTriggerAudit}
            disabled={auditing}
            className="flex items-center space-x-2 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white px-5 py-2.5 rounded-xl text-xs sm:text-sm font-semibold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            <span>{auditing ? 'Auditing...' : 'Run Ollama AI Audit'}</span>
          </button>
        </div>
      </div>

      {/* Season Tabs */}
      <div className="flex items-center space-x-2 overflow-x-auto pb-4 mb-4">
        {seasons.map(sNum => (
          <button
            key={sNum}
            onClick={() => setSelectedSeason(sNum)}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition-all whitespace-nowrap ${
              selectedSeason === sNum
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/20'
                : 'bg-dark-800 text-slate-400 hover:text-slate-200 hover:bg-dark-700 border border-dark-700'
            }`}
          >
            {sNum === 0 ? 'Specials (Season 0)' : `Season ${sNum}`}
          </button>
        ))}
      </div>

      {/* Episodes Inspector Table / Accordion */}
      <div className="space-y-3">
        {currentEpisodes.length === 0 ? (
          <div className="text-center py-12 text-slate-400 bg-dark-800/40 rounded-2xl border border-dark-700">
            No episodes found for this season.
          </div>
        ) : (
          currentEpisodes.map(ep => {
            const isExpanded = expandedEpisodeId === ep.id;
            return (
              <div
                key={ep.id}
                className={`rounded-2xl border transition-all overflow-hidden ${
                  isExpanded
                    ? 'bg-dark-800 border-indigo-500/50 shadow-xl'
                    : 'bg-dark-800/70 border-dark-700 hover:border-dark-600'
                }`}
              >
                {/* Episode Row Header */}
                <div
                  onClick={() => setExpandedEpisodeId(isExpanded ? null : ep.id)}
                  className="p-4 flex items-center justify-between cursor-pointer select-none"
                >
                  <div className="flex items-center space-x-4">
                    <button className="text-slate-400">
                      {isExpanded ? <ChevronDown className="w-5 h-5 text-indigo-400" /> : <ChevronRight className="w-5 h-5" />}
                    </button>

                    <div className="flex items-center space-x-3">
                      <span className="font-mono text-xs font-bold px-2 py-1 rounded bg-dark-900 border border-dark-700 text-indigo-300">
                        S{String(ep.season_number).padStart(2, '0')}E{String(ep.episode_number).padStart(2, '0')}
                      </span>
                      <h3 className="text-sm font-bold text-white hover:text-indigo-300 transition-colors">
                        {ep.title}
                      </h3>
                      {ep.air_date && <span className="text-xs text-slate-500 font-mono">{ep.air_date}</span>}
                    </div>
                  </div>

                  <div className="flex items-center space-x-3">
                    <span className="text-xs text-slate-400">
                      {ep.source_variations.length} source{ep.source_variations.length === 1 ? '' : 's'} mapped
                    </span>
                    <StatusBadge status={ep.ai_verification_status} confidence={ep.ai_confidence_score} />
                  </div>
                </div>

                {/* Expanded Multi-Source Comparison Grid */}
                {isExpanded && (
                  <div className="p-6 border-t border-dark-700 bg-dark-900/60 space-y-6 animate-fade-in">
                    {/* Sonarr Canonical Base Info */}
                    <div className="p-4 rounded-xl bg-dark-800 border border-dark-700">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold uppercase tracking-wider text-indigo-400 flex items-center space-x-1.5">
                          <Layers className="w-3.5 h-3.5" />
                          <span>Sonarr Canonical Metadata</span>
                        </span>
                        <span className="text-[11px] font-mono text-slate-500">ID: {ep.sonarr_episode_id}</span>
                      </div>
                      <h4 className="text-sm font-bold text-white">{ep.title}</h4>
                      <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                        {ep.overview || 'No synopsis available in Sonarr.'}
                      </p>
                    </div>

                    {/* AI Audit Notes if any */}
                    {ep.ai_audit_notes && (
                      <div className="p-4 rounded-xl bg-indigo-950/40 border border-indigo-500/30 flex items-start space-x-3 text-xs text-indigo-200">
                        <Sparkles className="w-4 h-4 text-indigo-400 flex-shrink-0 mt-0.5" />
                        <div>
                          <strong className="font-semibold block mb-0.5">Ollama AI Audit Reasoning:</strong>
                          <span>{ep.ai_audit_notes}</span>
                        </div>
                      </div>
                    )}

                    {/* Multi-Source Variations Grid */}
                    <div>
                      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
                        Source Variations & Transcripts ({ep.source_variations.length})
                      </h4>

                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {ep.source_variations.map(source => (
                          <div
                            key={source.id}
                            className="p-4 rounded-xl bg-dark-800/90 border border-dark-700 flex flex-col justify-between"
                          >
                            <div>
                              <div className="flex items-center justify-between mb-2">
                                <span className="px-2 py-0.5 rounded bg-dark-700 text-indigo-300 border border-dark-600 text-[10px] font-mono font-bold uppercase">
                                  {source.source_name}
                                </span>
                                <span className="text-[10px] text-slate-400 font-mono">
                                  S{source.source_season_number}E{source.source_episode_number}
                                </span>
                              </div>

                              <h5 className="text-xs font-bold text-white mb-1">
                                {source.title || 'Untitled'}
                              </h5>

                              <p className="text-[11px] text-slate-400 line-clamp-3 leading-relaxed mb-3">
                                {source.overview || 'No synopsis provided by this source.'}
                              </p>

                              {source.has_transcript && (
                                <div className="p-2.5 rounded-lg bg-dark-900 border border-dark-700 text-[11px] text-slate-300 mb-2">
                                  <div className="flex items-center justify-between mb-1">
                                    <span className="font-semibold text-emerald-400 flex items-center space-x-1">
                                      <FileText className="w-3 h-3" />
                                      <span>Transcript Available</span>
                                    </span>
                                  </div>
                                  <p className="line-clamp-2 text-slate-400 text-[10px] italic">
                                    "{source.transcript_preview || 'Dialogue extracted'}"
                                  </p>
                                </div>
                              )}
                            </div>

                            <div className="pt-2 border-t border-dark-700/60 flex items-center justify-between text-[10px] text-slate-500 font-mono">
                              <span>Match: <strong>{source.match_method}</strong></span>
                              <span>Conf: <strong>{Math.round(source.match_confidence * 100)}%</strong></span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
