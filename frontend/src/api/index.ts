import { request, unwrapEntity, unwrapList } from './client'
import type {
  Channel,
  ChannelInput,
  EntityId,
  MessageResponse,
  PlaylistItem,
  RegistrationPayload,
  RegistrationResponse,
  SetupPayload,
  SetupStatus,
  Track,
  TrackInput,
  UploadJob,
  UploadPreflightResponse,
  UploadQueueSnapshot,
  User,
  UserStatus,
  CredentialsPayload,
} from './types'

const id = (value: EntityId) => encodeURIComponent(String(value))

export const api = {
  setup: {
    async status(): Promise<SetupStatus> {
      const payload = await request<unknown>('/api/setup/status')
      return unwrapEntity<SetupStatus>(payload, ['setup'])
    },
    async create(input: SetupPayload): Promise<User | MessageResponse | undefined> {
      const payload = await request<unknown>('/api/setup', { method: 'POST', json: input })
      return unwrapEntity<User | MessageResponse | undefined>(payload, ['user'])
    },
  },

  auth: {
    async register(input: RegistrationPayload): Promise<RegistrationResponse | undefined> {
      const payload = await request<unknown>('/api/auth/register', { method: 'POST', json: input })
      return unwrapEntity<RegistrationResponse | undefined>(payload)
    },
    async login(input: CredentialsPayload): Promise<User> {
      const payload = await request<unknown>('/api/auth/login', { method: 'POST', json: input })
      const user = unwrapEntity<User | undefined>(payload, ['user'])
      if (user?.id !== undefined) return user
      const current = await request<unknown>('/api/auth/me')
      return unwrapEntity<User>(current, ['user'])
    },
    async logout(): Promise<void> {
      await request('/api/auth/logout', { method: 'POST' })
    },
    async me(): Promise<User> {
      const payload = await request<unknown>('/api/auth/me')
      return unwrapEntity<User>(payload, ['user'])
    },
  },

  channels: {
    async list(): Promise<Channel[]> {
      return unwrapList<Channel>(await request<unknown>('/api/channels'), ['channels'])
    },
    async get(channelId: EntityId): Promise<Channel> {
      const payload = await request<unknown>(`/api/channels/${id(channelId)}`)
      return unwrapEntity<Channel>(payload, ['channel'])
    },
    async playlist(channelId: EntityId): Promise<PlaylistItem[]> {
      const payload = await request<unknown>(`/api/channels/${id(channelId)}/playlist`)
      return unwrapList<PlaylistItem>(payload, ['playlist'])
    },
  },

  admin: {
    async users(): Promise<User[]> {
      return unwrapList<User>(await request<unknown>('/api/admin/users'), ['users'])
    },
    async updateUser(userId: EntityId, status: UserStatus): Promise<User | undefined> {
      const payload = await request<unknown>(`/api/admin/users/${id(userId)}`, {
        method: 'PATCH',
        json: { status },
      })
      return unwrapEntity<User | undefined>(payload, ['user'])
    },
    async promoteUser(userId: EntityId): Promise<User | undefined> {
      const payload = await request<unknown>(`/api/admin/users/${id(userId)}/role`, {
        method: 'PATCH',
        json: { role: 'admin' },
      })
      return unwrapEntity<User | undefined>(payload, ['user'])
    },
    async deleteUser(userId: EntityId): Promise<void> {
      await request(`/api/admin/users/${id(userId)}`, { method: 'DELETE' })
    },

    async channels(): Promise<Channel[]> {
      return unwrapList<Channel>(await request<unknown>('/api/admin/channels'), ['channels'])
    },
    async createChannel(input: ChannelInput): Promise<Channel | undefined> {
      const payload = await request<unknown>('/api/admin/channels', { method: 'POST', json: input })
      return unwrapEntity<Channel | undefined>(payload, ['channel'])
    },
    async updateChannel(channelId: EntityId, input: Partial<ChannelInput>): Promise<Channel | undefined> {
      const payload = await request<unknown>(`/api/admin/channels/${id(channelId)}`, {
        method: 'PATCH',
        json: input,
      })
      return unwrapEntity<Channel | undefined>(payload, ['channel'])
    },
    async deleteChannel(channelId: EntityId): Promise<void> {
      await request(`/api/admin/channels/${id(channelId)}`, { method: 'DELETE' })
    },

    async tracks(): Promise<Track[]> {
      return unwrapList<Track>(await request<unknown>('/api/admin/tracks'), ['tracks'])
    },
    async uploadQueue(): Promise<UploadQueueSnapshot> {
      return request<UploadQueueSnapshot>('/api/admin/uploads')
    },
    async preflightUploads(filenames: string[]): Promise<UploadPreflightResponse> {
      return request<UploadPreflightResponse>('/api/admin/uploads/preflight', {
        method: 'POST',
        json: { filenames },
      })
    },
    async reserveUpload(
      clientId: string,
      file: File,
      confirmSimilar = false,
    ): Promise<UploadJob> {
      const payload = await request<unknown>('/api/admin/uploads', {
        method: 'POST',
        json: {
          client_id: clientId,
          filename: file.name,
          size_bytes: file.size,
          confirm_similar: confirmSimilar,
        },
      })
      return unwrapEntity<UploadJob>(payload, ['job'])
    },
    async heartbeatUploads(clientId: string): Promise<void> {
      await request('/api/admin/uploads/heartbeat', {
        method: 'POST',
        json: { client_id: clientId },
      })
    },
    async cancelUpload(jobId: string): Promise<void> {
      await request(`/api/admin/uploads/${encodeURIComponent(jobId)}`, {
        method: 'DELETE',
      })
    },
    async updateTrack(trackId: EntityId, input: Partial<TrackInput>): Promise<Track | undefined> {
      const payload = await request<unknown>(`/api/admin/tracks/${id(trackId)}`, {
        method: 'PATCH',
        json: input,
      })
      return unwrapEntity<Track | undefined>(payload, ['track'])
    },
    async uploadTrackCover(trackId: EntityId, file: File): Promise<Track | undefined> {
      const form = new FormData()
      form.append('file', file, file.name)
      const payload = await request<unknown>(`/api/admin/tracks/${id(trackId)}/cover`, {
        method: 'POST',
        body: form,
      })
      return unwrapEntity<Track | undefined>(payload, ['track'])
    },
    async deleteTrack(trackId: EntityId): Promise<void> {
      await request(`/api/admin/tracks/${id(trackId)}`, { method: 'DELETE' })
    },

    async playlist(channelId: EntityId): Promise<PlaylistItem[]> {
      const payload = await request<unknown>(`/api/admin/channels/${id(channelId)}/playlist`)
      return unwrapList<PlaylistItem>(payload, ['playlist'])
    },
    async addPlaylistItem(channelId: EntityId, trackId: EntityId): Promise<PlaylistItem | undefined> {
      const payload = await request<unknown>(`/api/admin/channels/${id(channelId)}/playlist`, {
        method: 'POST',
        json: { track_id: trackId },
      })
      return unwrapEntity<PlaylistItem | undefined>(payload, ['item', 'playlist_item'])
    },
    async addPlaylistItems(channelId: EntityId, trackIds: EntityId[]): Promise<PlaylistItem[]> {
      const payload = await request<unknown>(
        `/api/admin/channels/${id(channelId)}/playlist/batch`,
        {
          method: 'POST',
          json: { track_ids: trackIds },
        },
      )
      return unwrapList<PlaylistItem>(payload, ['items'])
    },
    async updatePlaylistItem(
      channelId: EntityId,
      itemId: EntityId,
      input: { position?: number },
    ): Promise<PlaylistItem | undefined> {
      const payload = await request<unknown>(
        `/api/admin/channels/${id(channelId)}/playlist/${id(itemId)}`,
        { method: 'PATCH', json: input },
      )
      return unwrapEntity<PlaylistItem | undefined>(payload, ['item', 'playlist_item'])
    },
    async removePlaylistItem(channelId: EntityId, itemId: EntityId): Promise<void> {
      await request(`/api/admin/channels/${id(channelId)}/playlist/${id(itemId)}`, {
        method: 'DELETE',
      })
    },
    async reorderPlaylist(channelId: EntityId, itemIds: EntityId[]): Promise<PlaylistItem[]> {
      const payload = await request<unknown>(
        `/api/admin/channels/${id(channelId)}/playlist/reorder`,
        { method: 'POST', json: { item_ids: itemIds } },
      )
      return unwrapList<PlaylistItem>(payload, ['playlist'])
    },
    async skip(channelId: EntityId): Promise<MessageResponse | undefined> {
      const payload = await request<unknown>(`/api/admin/channels/${id(channelId)}/skip`, {
        method: 'POST',
      })
      return unwrapEntity<MessageResponse | undefined>(payload)
    },
    async playNow(channelId: EntityId, itemId: EntityId): Promise<MessageResponse | undefined> {
      const payload = await request<unknown>(
        `/api/admin/channels/${id(channelId)}/play-now/${id(itemId)}`,
        { method: 'POST' },
      )
      return unwrapEntity<MessageResponse | undefined>(payload)
    },
  },
}
