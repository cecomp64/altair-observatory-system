import { Controller } from "@hotwired/stimulus"

// A table row that opens its link when clicked anywhere. The link stays a real
// link (keyboard, middle-click and "open in new tab" on it work as usual); a
// click elsewhere in the row is forwarded to it, and a modified or middle click
// opens it in a new tab. Safari doesn't position content against a <tr>, so a
// CSS overlay stretched over the row can't do this.
export default class extends Controller {
  static targets = ["link"]

  open(event) {
    if (event.target.closest("a, button, input, select, label")) return
    if (event.button > 1 || window.getSelection().toString()) return
    if (event.button === 1 || event.metaKey || event.ctrlKey || event.shiftKey) {
      window.open(this.linkTarget.href, "_blank", "noopener")
    } else {
      this.linkTarget.click()
    }
  }
}
