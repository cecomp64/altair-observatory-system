import { Controller } from "@hotwired/stimulus"
import { Chart, registerables } from "chart.js"

Chart.register(...registerables)

// Renders a Chart.js chart from data embedded in the DOM.
//
//   <canvas data-controller="chart"
//           data-chart-type-value="doughnut"
//           data-chart-data-value="<%= { labels: [...], datasets: [...] }.to_json %>">
//   </canvas>
export default class extends Controller {
  static values = { type: String, data: Object, options: Object }

  connect() {
    this.chart = new Chart(this.element, {
      type: this.typeValue || "bar",
      data: this.dataValue,
      options: Object.assign({ responsive: true, maintainAspectRatio: false }, this.optionsValue || {})
    })
  }

  disconnect() {
    this.chart?.destroy()
  }
}
