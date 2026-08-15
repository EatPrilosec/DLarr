import { AppSettings, ConnectionTestResponse, Show, SonarrShowLookup, Job, Episode } from '../types';

const API_BASE = '/api/v1';

export const api = {
  // Settings
  async getSettings(): Promise<AppSettings> {
    const res = await fetch(`${API_BASE}/settings`);
    if (!res.ok) throw new Error('Failed to load settings');
    return res.json();
  },

  async updateSettings(settings: AppSettings): Promise<AppSettings> {
    const res = await fetch(`${API_BASE}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    if (!res.ok) throw new Error('Failed to save settings');
    return res.json();
  },

  async testConnection(service: string, config: Record<string, any>): Promise<ConnectionTestResponse> {
    const res = await fetch(`${API_BASE}/settings/test-connection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service, config }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Test connection failed' }));
      throw new Error(err.detail || 'Connection test failed');
    }
    return res.json();
  },

  // Shows
  async getShows(): Promise<Show[]> {
    const res = await fetch(`${API_BASE}/shows`);
    if (!res.ok) throw new Error('Failed to fetch shows');
    return res.json();
  },

  async getShow(id: number): Promise<Show> {
    const res = await fetch(`${API_BASE}/shows/${id}`);
    if (!res.ok) throw new Error('Failed to fetch show detail');
    return res.json();
  },

  async deleteShow(id: number): Promise<void> {
    const res = await fetch(`${API_BASE}/shows/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete show');
  },

  async lookupSonarrShows(): Promise<SonarrShowLookup[]> {
    const res = await fetch(`${API_BASE}/shows/sonarr-lookup`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to query Sonarr' }));
      throw new Error(err.detail || 'Failed to query Sonarr');
    }
    return res.json();
  },

  async importShow(sonarr_series_id: number): Promise<{ success: boolean; job_id: number; message: string }> {
    const res = await fetch(`${API_BASE}/shows/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sonarr_series_id }),
    });
    if (!res.ok) throw new Error('Failed to initiate show import');
    return res.json();
  },

  // Episodes
  async getEpisode(id: number): Promise<Episode> {
    const res = await fetch(`${API_BASE}/episodes/${id}`);
    if (!res.ok) throw new Error('Failed to fetch episode');
    return res.json();
  },

  async updateEpisodeStatus(
    id: number,
    data: { ai_verification_status?: string; ai_confidence_score?: number; ai_audit_notes?: string }
  ): Promise<Episode> {
    const res = await fetch(`${API_BASE}/episodes/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error('Failed to update episode');
    return res.json();
  },

  // Audit
  async triggerAudit(showId: number): Promise<{ success: boolean; job_id: number }> {
    const res = await fetch(`${API_BASE}/audit/${showId}`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to trigger show audit');
    return res.json();
  },

  // Jobs
  async getJobs(): Promise<Job[]> {
    const res = await fetch(`${API_BASE}/jobs`);
    if (!res.ok) throw new Error('Failed to fetch jobs');
    return res.json();
  },

  async getJob(id: number): Promise<Job> {
    const res = await fetch(`${API_BASE}/jobs/${id}`);
    if (!res.ok) throw new Error('Failed to fetch job');
    return res.json();
  },
};
