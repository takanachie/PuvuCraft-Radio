export type EntityId = string | number
export type UserRole = 'admin' | 'listener'
export type UserStatus = 'pending' | 'approved' | 'rejected' | 'disabled'
export type PlaybackMode = 'sequential' | 'shuffle'
export type ChannelState = 'starting' | 'live' | 'degraded' | 'offline' | 'stopped'
export type UploadJobStatus =
  | 'queued'
  | 'ready'
  | 'uploading'
  | 'verifying'
  | 'normalizing'
  | 'placing'
  | 'completed'
  | 'failed'
  | 'rejected'
  | 'cancelled'
  | 'expired'

export interface SetupStatus {
  required: boolean
}

export interface SetupPayload {
  token: string
  username: string
  email: string
  password: string
}

export interface CredentialsPayload {
  username: string
  password: string
}

export interface RegistrationPayload extends CredentialsPayload {
  email: string
}

export interface RegistrationResponse {
  status?: UserStatus | 'pending_approval'
  message?: string
  user?: User
}

export interface User {
  id: EntityId
  username: string
  email: string
  role: UserRole
  status: UserStatus
  enabled?: boolean
  is_active?: boolean
  created_at?: string
  updated_at?: string
  approved_at?: string | null
  last_login_at?: string | null
}

export interface TrackSummary {
  id: EntityId
  title: string
  artist?: string | null
  album?: string | null
  duration_seconds?: number | null
  cover_url?: string | null
  available?: boolean
}

export interface Track extends TrackSummary {
  library_group?: string
  original_filename?: string | null
  file_size_bytes?: number | null
  sha256?: string | null
  mime_type?: string | null
  sample_rate?: number | null
  channels?: number | null
  bits_per_sample?: number | null
  normalized?: boolean
  storage_id?: string | null
  created_at?: string
  updated_at?: string
  unavailable_reason?: string | null
  referenced_by?: Array<{ id: EntityId; name: string }>
}

export interface TrackPage {
  items: Track[]
  page: number
  page_size: number
  total: number
  total_pages: number
  library_group: string
  library_groups: string[]
  available_count: number
  unavailable_count: number
}

export interface TrackQuery {
  page?: number
  libraryGroup?: string
  search?: string
  availableOnly?: boolean
  excludeChannelId?: EntityId
}

export interface TrackLibraryMoveResponse {
  moved: number
  source_library: string
  target_library: string
  library_groups: string[]
}

export interface MusicLibrary {
  name: string
  track_count: number
  created_at?: string
  updated_at?: string
}

export interface PlaybackState {
  channel_id?: EntityId
  status: ChannelState | string
  current_item_id?: EntityId | null
  current_track?: TrackSummary | null
  position_seconds?: number
  elapsed_seconds?: number
  server_position_seconds?: number
  offset_seconds?: number
  duration_seconds?: number | null
  server_time?: string
  started_at?: string | null
  listener_count?: number
  last_error?: string | null
}

export interface ChannelHealth {
  status?: ChannelState | string
  ffmpeg_running?: boolean
  last_started_at?: string | null
  restart_count?: number
  last_error?: string | null
  recent_history?: Array<{
    id?: EntityId
    track?: TrackSummary | null
    started_at?: string
    ended_at?: string | null
    reason?: string | null
  }>
}

export interface Channel {
  id: EntityId
  name: string
  slug: string
  description?: string | null
  enabled: boolean
  playback_mode: PlaybackMode
  display_order?: number
  status?: ChannelState | string
  current_track?: TrackSummary | null
  playback?: PlaybackState | null
  playback_state?: PlaybackState | null
  health?: ChannelHealth | null
  listener_count?: number
  last_error?: string | null
  created_at?: string
  updated_at?: string
}

export interface PlaylistItem {
  id: EntityId
  item_id?: EntityId
  position?: number
  track_id?: EntityId
  track?: TrackSummary
  title?: string
  artist?: string | null
  album?: string | null
  duration_seconds?: number | null
  cover_url?: string | null
  available?: boolean
  is_current?: boolean
  added_at?: string
}

export interface PlaybackEvent {
  type?: string
  event?: string
  data?: unknown
  channel?: Partial<Channel>
  playback?: Partial<PlaybackState>
  playback_state?: Partial<PlaybackState>
  state?: Partial<PlaybackState>
  current_track?: TrackSummary | null
  track?: TrackSummary | null
  current_item_id?: EntityId | null
  status?: ChannelState | string
  position_seconds?: number
  elapsed_seconds?: number
  duration_seconds?: number | null
  server_time?: string
  listener_count?: number
  last_error?: string | null
}

export interface ChannelInput {
  name: string
  slug: string
  description: string
  enabled: boolean
  playback_mode: PlaybackMode
  display_order: number
}

export interface TrackInput {
  title: string
  artist: string
  album: string
  cover_url?: string | null
}

export interface SimilarTrackCandidate {
  id: EntityId
  title: string
  artist: string
  album: string
  original_filename: string
  duration_seconds: number
  similarity: number
}

export interface UploadPreflightFile {
  filename: string
  candidates: SimilarTrackCandidate[]
}

export interface UploadPreflightResponse {
  files: UploadPreflightFile[]
}

export interface UploadJob {
  id: string
  owner: {
    id: EntityId
    username: string
  }
  client_id: string
  original_filename: string
  declared_size_bytes: number
  bytes_received: number
  status: UploadJobStatus
  queue_position: number | null
  storage_id: string | null
  storage_name: string | null
  sha256: string | null
  track_id: EntityId | null
  duplicate: boolean
  error_code: string | null
  error_message: string | null
  ready_at: string | null
  lease_expires_at: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface UploadQueueSnapshot {
  jobs: UploadJob[]
  queue_limit: number
  max_concurrent: number
  active_count: number
  available_slots: number
  heartbeat_interval_seconds: number
}

export interface MessageResponse {
  message?: string
  status?: string
}
