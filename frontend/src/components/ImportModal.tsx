import React, { useState, useEffect } from 'react';
import { X, Search, Tv, Check, Loader2, AlertCircle, Settings2 } from 'lucide-react';
import { api } from '../services/api';
import { SonarrShowLookup } from '../types';

interface ImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onShowImported: (jobId: number) => void;
}

export const ImportModal: React.FC<ImportModalProps> = ({ isOpen, onClose, onShowImported }) => {
  const [shows, setShows] = useState<SonarrShowLookup[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [importingId, setImportingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Scan Options State
  const [scanMode, setScanMode] = useState<'full' | 'none' | 'custom'>('full');
  const [selectedSources, setSelectedSources] = useState<{ [key: string]: boolean }>({
    tmdb: true,
    tvmaze: true,
    omdb: true,
  });

  useEffect(() => {
    if (isOpen) {
      loadSonarrShows();
    }
  }, [isOpen]);

  const loadSonarrShows = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.lookupSonarrShows();
      setShows(data);
    } catch (err: any) {
      setError(err.message || 'Failed to connect to Sonarr. Please verify settings.');
    } finally {
      setLoading(false);
    }
  };

  const toggleSource = (src: string) => {
    setSelectedSources(prev => ({
      ...prev,
      [src]: !prev[src],
    }));
  };

  const handleImport = async (showId: number) => {
    setImportingId(showId);
    try {
      const activeSources = Object.keys(selectedSources).filter(k => selectedSources[k]);
      const res = await api.importShow(showId, {
        scan_mode: scanMode,
        sources: scanMode === 'none' ? [] : activeSources,
      });
      onShowImported(res.job_id);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to import show');
    } finally {
      setImportingId(null);
    }
  };

  if (!isOpen) return null;

  const filteredShows = shows.filter(s =>
    s.title.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="bg-dark-800 border border-dark-700 w-full max-w-3xl rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-dark-700 flex items-center justify-between bg-dark-800/90">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
              <Tv className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Import Show from Sonarr</h2>
              <p className="text-xs text-slate-400">Select a series to build the multi-source episode database and configure scan options</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-2 rounded-lg hover:bg-dark-700 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Global Scan Options */}
        <div className="px-6 py-3 border-b border-dark-700 bg-dark-900/60 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex items-center space-x-2">
            <Settings2 className="w-4 h-4 text-indigo-400" />
            <span className="font-semibold text-white">Scan Options:</span>
            <div className="flex items-center bg-dark-800 p-1 rounded-lg border border-dark-600 space-x-1">
              <button
                onClick={() => setScanMode('full')}
                className={`px-2.5 py-1 rounded-md transition-all font-medium ${
                  scanMode === 'full'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Full Scan (All)
              </button>
              <button
                onClick={() => setScanMode('none')}
                className={`px-2.5 py-1 rounded-md transition-all font-medium ${
                  scanMode === 'none'
                    ? 'bg-amber-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                No Scan (Metadata Only)
              </button>
              <button
                onClick={() => setScanMode('custom')}
                className={`px-2.5 py-1 rounded-md transition-all font-medium ${
                  scanMode === 'custom'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Custom Sources
              </button>
            </div>
          </div>

          {scanMode === 'custom' && (
            <div className="flex items-center space-x-3 bg-dark-800 px-3 py-1.5 rounded-lg border border-dark-600">
              <span className="text-slate-400">Sources:</span>
              <label className="flex items-center space-x-1.5 cursor-pointer text-slate-300 hover:text-white">
                <input
                  type="checkbox"
                  checked={selectedSources.tmdb}
                  onChange={() => toggleSource('tmdb')}
                  className="rounded bg-dark-900 border-dark-600 text-indigo-600 focus:ring-0"
                />
                <span>TMDB</span>
              </label>
              <label className="flex items-center space-x-1.5 cursor-pointer text-slate-300 hover:text-white">
                <input
                  type="checkbox"
                  checked={selectedSources.tvmaze}
                  onChange={() => toggleSource('tvmaze')}
                  className="rounded bg-dark-900 border-dark-600 text-indigo-600 focus:ring-0"
                />
                <span>TVmaze</span>
              </label>
              <label className="flex items-center space-x-1.5 cursor-pointer text-slate-300 hover:text-white">
                <input
                  type="checkbox"
                  checked={selectedSources.omdb}
                  onChange={() => toggleSource('omdb')}
                  className="rounded bg-dark-900 border-dark-600 text-indigo-600 focus:ring-0"
                />
                <span>OMDb</span>
              </label>
            </div>
          )}
        </div>

        {/* Search & Status */}
        <div className="p-6 border-b border-dark-700 bg-dark-900/40">
          <div className="relative">
            <Search className="w-5 h-5 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search shows from Sonarr library..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-11 pr-4 py-2.5 bg-dark-800 border border-dark-600 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 text-sm"
            />
          </div>

          {error && (
            <div className="mt-3 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center space-x-2 text-rose-400 text-xs">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Show List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-3">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 space-y-3 text-slate-400">
              <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
              <p className="text-sm">Fetching series list from Sonarr...</p>
            </div>
          ) : filteredShows.length === 0 ? (
            <div className="text-center py-16 text-slate-400">
              <Tv className="w-12 h-12 mx-auto text-slate-600 mb-2 opacity-50" />
              <p className="text-sm">No shows found in Sonarr.</p>
            </div>
          ) : (
            filteredShows.map(show => (
              <div
                key={show.id}
                className="flex items-center justify-between p-3.5 rounded-xl bg-dark-700/40 hover:bg-dark-700/70 border border-dark-600/60 transition-all group"
              >
                <div className="flex items-center space-x-4">
                  {show.poster_url ? (
                    <img
                      src={show.poster_url}
                      alt={show.title}
                      className="w-12 h-16 object-cover rounded-lg shadow-md border border-dark-600"
                    />
                  ) : (
                    <div className="w-12 h-16 rounded-lg bg-dark-800 border border-dark-600 flex items-center justify-center text-slate-600">
                      <Tv className="w-6 h-6" />
                    </div>
                  )}
                  <div>
                    <h3 className="text-sm font-bold text-white group-hover:text-indigo-400 transition-colors">
                      {show.title} {show.year && <span className="text-xs text-slate-400 font-normal">({show.year})</span>}
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5 line-clamp-1 max-w-md">{show.overview || 'No overview available'}</p>
                    <div className="flex items-center space-x-3 mt-1 text-[11px] text-slate-500">
                      <span>{show.episode_count} Episodes</span>
                      {show.tvdb_id && <span>TVDB: {show.tvdb_id}</span>}
                      {show.imdb_id && <span>IMDb: {show.imdb_id}</span>}
                    </div>
                  </div>
                </div>

                <div>
                  {show.is_imported ? (
                    <span className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs font-semibold">
                      <Check className="w-4 h-4" />
                      <span>Imported</span>
                    </span>
                  ) : (
                    <button
                      onClick={() => handleImport(show.id)}
                      disabled={importingId === show.id}
                      className="flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition-all shadow-md shadow-indigo-600/20 disabled:opacity-50"
                    >
                      {importingId === show.id ? (
                        <>
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>Importing...</span>
                        </>
                      ) : (
                        <span>Build DB</span>
                      )}
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
