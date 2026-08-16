import React, { useState, useEffect } from 'react';
import { Save, CheckCircle2, XCircle, Loader2, Sparkles, Server, Film, FileText, AlertCircle, Key, Cpu, Plus, Trash2 } from 'lucide-react';
import { api } from '../services/api';
import { AppSettings, ConnectionTestResponse } from '../types';

export const Settings: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings>({
    ollama_url: 'http://localhost:11434',
    ollama_primary_model: 'gemma4:e2b',
    ollama_fallback_model: 'Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M',
    ollama_fallback_models: ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M'],
    ai_batch_size: 10,
    sonarr_url: '',
    sonarr_api_key: '',
    tmdb_api_key: '',
    tvmaze_api_key: '',
    omdb_api_key: '',
    subdl_api_key: '',
    opensubtitles_api_key: '',
    opensubtitles_user_agent: 'DLarr v0.1',
    max_concurrent_jobs: 1,
    max_concurrent_ollama_requests: 1,
  });

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, ConnectionTestResponse>>({});
  const [testingService, setTestingService] = useState<string | null>(null);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const data = await api.getSettings();
      const fbList = data.ollama_fallback_models && data.ollama_fallback_models.length > 0
        ? data.ollama_fallback_models
        : (data.ollama_fallback_model ? [data.ollama_fallback_model] : ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M']);

      setSettings({
        ...data,
        ollama_fallback_models: fbList,
        ollama_fallback_model: fbList[0],
        ai_batch_size: data.ai_batch_size || 10,
      });
    } catch (err) {
      console.error('Failed to load settings:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAddFallbackModel = () => {
    const current = settings.ollama_fallback_models || ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M'];
    setSettings({
      ...settings,
      ollama_fallback_models: [...current, ''],
    });
  };

  const handleUpdateFallbackModel = (index: number, val: string) => {
    const current = [...(settings.ollama_fallback_models || ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M'])];
    current[index] = val;
    setSettings({
      ...settings,
      ollama_fallback_models: current,
      ollama_fallback_model: current[0] || 'Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M',
    });
  };

  const handleRemoveFallbackModel = (index: number) => {
    const current = settings.ollama_fallback_models || ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M'];
    if (current.length <= 1) return; // Never allow 0 fallback models
    const updated = current.filter((_, idx) => idx !== index);
    setSettings({
      ...settings,
      ollama_fallback_models: updated,
      ollama_fallback_model: updated[0] || 'Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M',
    });
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveSuccess(false);
    try {
      const cleanFallbacks = (settings.ollama_fallback_models || [])
        .map(m => m.trim())
        .filter(m => m.length > 0);
      
      const safeFallbacks = cleanFallbacks.length > 0 ? cleanFallbacks : ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M'];

      const payload: AppSettings = {
        ...settings,
        ollama_fallback_models: safeFallbacks,
        ollama_fallback_model: safeFallbacks[0],
        ai_batch_size: Math.max(1, settings.ai_batch_size || 10),
      };

      await api.updateSettings(payload);
      setSettings(payload);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      alert('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async (service: string) => {
    setTestingService(service);
    try {
      const res = await api.testConnection(service, settings);
      setTestResults(prev => ({ ...prev, [service]: res }));
    } catch (err: any) {
      setTestResults(prev => ({
        ...prev,
        [service]: { service, success: false, message: err.message || 'Connection test failed' },
      }));
    } finally {
      setTestingService(null);
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-12 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-extrabold text-white">Settings & Providers</h1>
          <p className="text-xs text-slate-400 mt-1">Configure your AI models, Sonarr connection, and metadata/transcript APIs</p>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center space-x-2 bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2.5 rounded-xl text-xs font-bold shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          <span>{saving ? 'Saving...' : 'Save Settings'}</span>
        </button>
      </div>

      {saveSuccess && (
        <div className="mb-6 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center space-x-3 text-emerald-400 text-xs font-semibold animate-fade-in">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>Settings saved successfully!</span>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Ollama AI Engine */}
        <div className="glass-panel rounded-2xl p-6 border border-dark-700">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <div className="p-2 rounded-xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
                <Sparkles className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base font-bold text-white">Ollama AI Connection</h2>
                <p className="text-xs text-slate-400">Primary and cascading fallback models for episode matching and consensus verification</p>
              </div>
            </div>

            <button
              type="button"
              onClick={() => handleTestConnection('ollama')}
              disabled={testingService === 'ollama'}
              className="px-3.5 py-1.5 rounded-xl bg-dark-700 hover:bg-dark-600 text-slate-200 text-xs font-semibold border border-dark-600 transition-colors flex items-center space-x-1.5"
            >
              {testingService === 'ollama' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>Test Connection</span>
            </button>
          </div>

          {testResults.ollama && (
            <div className={`mb-4 p-3 rounded-xl text-xs flex items-center space-x-2 ${
              testResults.ollama.success ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
            }`}>
              {testResults.ollama.success ? <CheckCircle2 className="w-4 h-4 flex-shrink-0" /> : <XCircle className="w-4 h-4 flex-shrink-0" />}
              <span>{testResults.ollama.message}</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Ollama Server URL</label>
              <input
                type="text"
                value={settings.ollama_url}
                onChange={e => setSettings({ ...settings, ollama_url: e.target.value })}
                placeholder="http://localhost:11434"
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Primary Model</label>
              <input
                type="text"
                value={settings.ollama_primary_model}
                onChange={e => setSettings({ ...settings, ollama_primary_model: e.target.value })}
                placeholder="gemma4:e2b"
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Dynamic Fallback Models List */}
          <div className="mt-4 pt-4 border-t border-dark-700/60">
            <div className="flex items-center justify-between mb-2">
              <div>
                <label className="block text-xs font-semibold text-slate-300">
                  Fallback Models (Priority Order)
                </label>
                <p className="text-[11px] text-slate-500">
                  Cascading fallback models tried in sequence if primary model fails or rejects a comparison (default: 1, minimum 1).
                </p>
              </div>
              <button
                type="button"
                onClick={handleAddFallbackModel}
                className="px-2.5 py-1 rounded-lg bg-dark-700 hover:bg-dark-600 text-indigo-300 text-xs font-semibold border border-dark-600 flex items-center space-x-1 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Fallback Model</span>
              </button>
            </div>

            <div className="space-y-2">
              {(settings.ollama_fallback_models || ['Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M']).map((fbModel, idx) => (
                <div key={idx} className="flex items-center space-x-2">
                  <span className="px-2 py-1 rounded bg-dark-800 text-slate-400 font-mono text-[11px] font-bold border border-dark-700 w-8 text-center">
                    #{idx + 1}
                  </span>
                  <input
                    type="text"
                    value={fbModel}
                    onChange={e => handleUpdateFallbackModel(idx, e.target.value)}
                    placeholder={idx === 0 ? "Gemma-4-E2B-it-uncensored-GGUF:Q4_K_M" : "e.g. mistral:7b"}
                    className="flex-1 px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
                  />
                  {(settings.ollama_fallback_models?.length || 1) > 1 && (
                    <button
                      type="button"
                      onClick={() => handleRemoveFallbackModel(idx)}
                      className="p-2 rounded-xl text-slate-500 hover:text-rose-400 hover:bg-dark-800 transition-colors"
                      title="Remove fallback model"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Available Models Quick-Select (if tested) */}
          {testResults.ollama?.available_models && testResults.ollama.available_models.length > 0 && (
            <div className="mt-4 pt-3 border-t border-dark-700/40">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                Available on Ollama Server (click to set):
              </span>
              <div className="flex flex-wrap gap-1.5 max-h-28 overflow-y-auto">
                {testResults.ollama.available_models.map(m => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      if (!settings.ollama_primary_model) {
                        setSettings({ ...settings, ollama_primary_model: m });
                      } else {
                        const current = settings.ollama_fallback_models || [];
                        if (!current.includes(m)) {
                          setSettings({ ...settings, ollama_fallback_models: [...current, m] });
                        }
                      }
                    }}
                    className="px-2 py-0.5 rounded-lg bg-dark-800 hover:bg-indigo-600/20 hover:text-indigo-300 text-slate-300 border border-dark-700 text-[10px] font-mono transition-colors"
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Concurrency & Resource Limits */}
        <div className="glass-panel rounded-2xl p-6 border border-dark-700">
          <div className="flex items-center space-x-3 mb-4">
            <div className="p-2 rounded-xl bg-purple-600/20 text-purple-400 border border-purple-500/30">
              <Cpu className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Concurrency & Matching Limits</h2>
              <p className="text-xs text-slate-400">Control background workers, parallel AI connections, and batch comparison sizes</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Max Concurrent Jobs</label>
              <input
                type="number"
                min="1"
                max="10"
                value={settings.max_concurrent_jobs || 1}
                onChange={e => setSettings({ ...settings, max_concurrent_jobs: Math.max(1, parseInt(e.target.value) || 1) })}
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
              <p className="text-[11px] text-slate-500 mt-1">Simultaneous show import/matching tasks (extra tasks wait in queue).</p>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Max Concurrent AI Requests</label>
              <input
                type="number"
                min="1"
                max="10"
                value={settings.max_concurrent_ollama_requests || 1}
                onChange={e => setSettings({ ...settings, max_concurrent_ollama_requests: Math.max(1, parseInt(e.target.value) || 1) })}
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
              <p className="text-[11px] text-slate-500 mt-1">Throttles parallel Ollama calls to prevent GPU/VRAM thrashing.</p>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">AI Matching Batch Size</label>
              <input
                type="number"
                min="1"
                max="50"
                value={settings.ai_batch_size || 10}
                onChange={e => setSettings({ ...settings, ai_batch_size: Math.max(1, parseInt(e.target.value) || 10) })}
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
              <p className="text-[11px] text-slate-500 mt-1">Number of episode pairs evaluated per LLM prompt in Step 1A, 1B, and 2B (default: 10).</p>
            </div>
          </div>
        </div>

        {/* Sonarr Connection */}
        <div className="glass-panel rounded-2xl p-6 border border-dark-700">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <div className="p-2 rounded-xl bg-sky-600/20 text-sky-400 border border-sky-500/30">
                <Server className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base font-bold text-white">Sonarr Connection</h2>
                <p className="text-xs text-slate-400">Canonical episode index and missing episode detection source</p>
              </div>
            </div>

            <button
              type="button"
              onClick={() => handleTestConnection('sonarr')}
              disabled={testingService === 'sonarr'}
              className="px-3.5 py-1.5 rounded-xl bg-dark-700 hover:bg-dark-600 text-slate-200 text-xs font-semibold border border-dark-600 transition-colors flex items-center space-x-1.5"
            >
              {testingService === 'sonarr' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              <span>Test Connection</span>
            </button>
          </div>

          {testResults.sonarr && (
            <div className={`mb-4 p-3 rounded-xl text-xs flex items-center space-x-2 ${
              testResults.sonarr.success ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
            }`}>
              {testResults.sonarr.success ? <CheckCircle2 className="w-4 h-4 flex-shrink-0" /> : <XCircle className="w-4 h-4 flex-shrink-0" />}
              <span>{testResults.sonarr.message}</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Sonarr URL</label>
              <input
                type="text"
                value={settings.sonarr_url}
                onChange={e => setSettings({ ...settings, sonarr_url: e.target.value })}
                placeholder="http://192.168.1.50:8989"
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Sonarr API Key</label>
              <input
                type="password"
                value={settings.sonarr_api_key}
                onChange={e => setSettings({ ...settings, sonarr_api_key: e.target.value })}
                placeholder="Enter Sonarr API Key"
                className="w-full px-3.5 py-2 rounded-xl bg-dark-900 border border-dark-700 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>
        </div>

        {/* Metadata Providers (TMDB, TVmaze, OMDb) */}
        <div className="glass-panel rounded-2xl p-6 border border-dark-700">
          <div className="flex items-center space-x-3 mb-4">
            <div className="p-2 rounded-xl bg-purple-600/20 text-purple-400 border border-purple-500/30">
              <Film className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Metadata Providers</h2>
              <p className="text-xs text-slate-400">External sources for title variations, alternate season numberings, and synopses</p>
            </div>
          </div>

          <div className="space-y-4">
            {/* TMDB */}
            <div className="p-4 rounded-xl bg-dark-900/60 border border-dark-700 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <span className="text-xs font-bold text-white">TheMovieDB (TMDB)</span>
                  <span className="text-[10px] text-slate-500 font-mono">v3 API</span>
                </div>
                <input
                  type="password"
                  value={settings.tmdb_api_key}
                  onChange={e => setSettings({ ...settings, tmdb_api_key: e.target.value })}
                  placeholder="TMDB API Key"
                  className="w-full px-3 py-1.5 rounded-lg bg-dark-800 border border-dark-600 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('tmdb')}
                disabled={testingService === 'tmdb'}
                className="self-start md:self-end px-3 py-1.5 rounded-lg bg-dark-800 hover:bg-dark-700 text-slate-300 text-xs font-medium border border-dark-600"
              >
                Test TMDB
              </button>
            </div>
            {testResults.tmdb && (
              <p className={`text-xs ${testResults.tmdb.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                {testResults.tmdb.message}
              </p>
            )}

            {/* TVmaze */}
            <div className="p-4 rounded-xl bg-dark-900/60 border border-dark-700 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <span className="text-xs font-bold text-white">TVmaze</span>
                  <span className="text-[10px] text-emerald-400 font-mono">Public REST (Free)</span>
                </div>
                <p className="text-xs text-slate-400">TVmaze does not require an API key for standard lookups.</p>
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('tvmaze')}
                disabled={testingService === 'tvmaze'}
                className="self-start md:self-end px-3 py-1.5 rounded-lg bg-dark-800 hover:bg-dark-700 text-slate-300 text-xs font-medium border border-dark-600"
              >
                Test TVmaze
              </button>
            </div>
            {testResults.tvmaze && (
              <p className={`text-xs ${testResults.tvmaze.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                {testResults.tvmaze.message}
              </p>
            )}

            {/* OMDb */}
            <div className="p-4 rounded-xl bg-dark-900/60 border border-dark-700 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <span className="text-xs font-bold text-white">OMDb API</span>
                  <span className="text-[10px] text-slate-500 font-mono">IMDb metadata</span>
                </div>
                <input
                  type="password"
                  value={settings.omdb_api_key}
                  onChange={e => setSettings({ ...settings, omdb_api_key: e.target.value })}
                  placeholder="OMDb API Key"
                  className="w-full px-3 py-1.5 rounded-lg bg-dark-800 border border-dark-600 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('omdb')}
                disabled={testingService === 'omdb'}
                className="self-start md:self-end px-3 py-1.5 rounded-lg bg-dark-800 hover:bg-dark-700 text-slate-300 text-xs font-medium border border-dark-600"
              >
                Test OMDb
              </button>
            </div>
            {testResults.omdb && (
              <p className={`text-xs ${testResults.omdb.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                {testResults.omdb.message}
              </p>
            )}
          </div>
        </div>

        {/* Transcript & Subtitle Providers */}
        <div className="glass-panel rounded-2xl p-6 border border-dark-700">
          <div className="flex items-center space-x-3 mb-4">
            <div className="p-2 rounded-xl bg-teal-600/20 text-teal-400 border border-teal-500/30">
              <FileText className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Transcript & Subtitle Providers</h2>
              <p className="text-xs text-slate-400">Sources for subtitle dialogues used for content and speech-based AI verification</p>
            </div>
          </div>

          <div className="space-y-4">
            {/* SubDL */}
            <div className="p-4 rounded-xl bg-dark-900/60 border border-dark-700 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <span className="text-xs font-bold text-white">SubDL API</span>
                  <span className="text-[10px] text-teal-400 font-mono">Free quota / Pro</span>
                </div>
                <input
                  type="password"
                  value={settings.subdl_api_key}
                  onChange={e => setSettings({ ...settings, subdl_api_key: e.target.value })}
                  placeholder="SubDL API Key"
                  className="w-full px-3 py-1.5 rounded-lg bg-dark-800 border border-dark-600 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('subdl')}
                disabled={testingService === 'subdl'}
                className="self-start md:self-end px-3 py-1.5 rounded-lg bg-dark-800 hover:bg-dark-700 text-slate-300 text-xs font-medium border border-dark-600"
              >
                Test SubDL
              </button>
            </div>
            {testResults.subdl && (
              <p className={`text-xs ${testResults.subdl.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                {testResults.subdl.message}
              </p>
            )}

            {/* OpenSubtitles */}
            <div className="p-4 rounded-xl bg-dark-900/60 border border-dark-700 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="flex-1">
                <div className="flex items-center space-x-2 mb-1">
                  <span className="text-xs font-bold text-white">OpenSubtitles.com</span>
                  <span className="text-[10px] text-slate-500 font-mono">REST v1</span>
                </div>
                <input
                  type="password"
                  value={settings.opensubtitles_api_key}
                  onChange={e => setSettings({ ...settings, opensubtitles_api_key: e.target.value })}
                  placeholder="OpenSubtitles API Key"
                  className="w-full px-3 py-1.5 rounded-lg bg-dark-800 border border-dark-600 text-white text-xs font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('opensubtitles')}
                disabled={testingService === 'opensubtitles'}
                className="self-start md:self-end px-3 py-1.5 rounded-lg bg-dark-800 hover:bg-dark-700 text-slate-300 text-xs font-medium border border-dark-600"
              >
                Test OpenSubtitles
              </button>
            </div>
            {testResults.opensubtitles && (
              <p className={`text-xs ${testResults.opensubtitles.success ? 'text-emerald-400' : 'text-rose-400'}`}>
                {testResults.opensubtitles.message}
              </p>
            )}
          </div>
        </div>
      </form>
    </div>
  );
};
