export interface AppSettings {
  ollama_url: string;
  ollama_primary_model: string;
  ollama_fallback_model: string;
  sonarr_url: string;
  sonarr_api_key: string;
  tmdb_api_key: string;
  tvmaze_api_key: string;
  omdb_api_key: string;
  subdl_api_key: string;
  opensubtitles_api_key: string;
  opensubtitles_user_agent: string;
}

export interface ConnectionTestResponse {
  service: string;
  success: boolean;
  message: string;
  details?: Record<string, any>;
  available_models?: string[];
}

export interface EpisodeSourceMetadata {
  id: number;
  episode_id: number;
  show_id: number;
  source_name: string;
  source_show_id?: string;
  source_episode_id?: string;
  source_season_number?: number;
  source_episode_number?: number;
  source_absolute_number?: number;
  title?: string;
  alternate_titles?: string;
  overview?: string;
  air_date?: string;
  runtime_mins?: number;
  has_transcript: boolean;
  transcript_preview?: string;
  transcript_full?: string;
  subtitle_language?: string;
  subtitle_format?: string;
  match_method: string;
  match_confidence: number;
  raw_metadata?: string;
  created_at: string;
  updated_at: string;
}

export interface Episode {
  id: number;
  show_id: number;
  sonarr_episode_id: number;
  season_number: number;
  episode_number: number;
  absolute_episode_number?: number;
  title: string;
  overview?: string;
  air_date?: string;
  has_file: boolean;
  monitored: boolean;
  ai_verification_status: string;
  ai_confidence_score: number;
  ai_audit_notes?: string;
  created_at: string;
  updated_at: string;
  source_variations: EpisodeSourceMetadata[];
}

export interface Show {
  id: number;
  sonarr_series_id: number;
  title: string;
  clean_title?: string;
  sort_title?: string;
  year?: number;
  status?: string;
  overview?: string;
  poster_url?: string;
  tvdb_id?: number;
  tmdb_id?: number;
  imdb_id?: string;
  tvmaze_id?: number;
  path?: string;
  monitored: boolean;
  audit_status: string;
  last_audited_at?: string;
  created_at: string;
  updated_at: string;
  episode_count: number;
  mapped_sources_summary?: {
    sources: string[];
  };
  episodes?: Episode[];
}

export interface SonarrShowLookup {
  id: number;
  title: string;
  year?: number;
  tvdb_id?: number;
  imdb_id?: string;
  tmdb_id?: number;
  overview?: string;
  poster_url?: string;
  episode_count: number;
  monitored: boolean;
  path?: string;
  is_imported: boolean;
}

export interface Job {
  id: number;
  show_id?: number;
  job_type: string;
  status: string;
  progress: number;
  message?: string;
  logs?: string;
  created_at: string;
  updated_at: string;
  finished_at?: string;
}
