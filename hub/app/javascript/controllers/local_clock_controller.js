import { Controller } from "@hotwired/stimulus"

// A clock in a given IANA time zone (the telescope's), updated every 30 s.
export default class extends Controller {
  static values = { zone: String }

  connect() {
    this.format = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: this.zoneValue, timeZoneName: "short" })
    this.tick()
    this.timer = setInterval(() => this.tick(), 30000)
  }

  disconnect() {
    clearInterval(this.timer)
  }

  tick() {
    const now = new Date()
    this.element.textContent = this.format.format(now)
    this.element.setAttribute("datetime", now.toISOString())
  }
}
