export class SingleFlightRequest {
  private controller: AbortController | null = null
  private timeout: ReturnType<typeof setTimeout> | null = null

  get active(): boolean {
    return this.controller !== null
  }

  async run(
    task: (signal: AbortSignal) => Promise<void>,
    timeoutMs: number,
  ): Promise<boolean> {
    if (this.controller) return false

    const controller = new AbortController()
    this.controller = controller
    this.timeout = setTimeout(() => controller.abort(), Math.max(1, timeoutMs))
    try {
      await task(controller.signal)
      return true
    } finally {
      if (this.controller === controller) this.controller = null
      if (this.timeout !== null) {
        clearTimeout(this.timeout)
        this.timeout = null
      }
    }
  }

  abort(): void {
    if (this.timeout !== null) {
      clearTimeout(this.timeout)
      this.timeout = null
    }
    this.controller?.abort()
  }
}
