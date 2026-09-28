import { Controller } from "@hotwired/stimulus"
import { Chart, registerables } from "chart.js"
import annotationPlugin from "chartjs-plugin-annotation"
import "chartjs-adapter-date-fns"

Chart.register(...registerables, annotationPlugin)

// Renders a Chart.js chart from data embedded in the DOM.
//
//   <canvas data-controller="chart"
//           data-chart-type-value="doughnut"
//           data-chart-data-value="<%= { labels: [...], datasets: [...] }.to_json %>">
//   </canvas>
//
// Options pass straight through, so time axes (`scales.x.type: "time"`) and
// annotations (`plugins.annotation`: twilight bands, the minimum-altitude
// line) are configured server-side. With `nowLine: true` a vertical line
// marks the current time and keeps moving; on a category axis of evenly
// spaced samples, `timeStart`/`timeStep` (epoch ms) place it.
//
// A data point's `tip` (for `{ x, y, tip }` points) replaces its tooltip
// line (and the title, when every point shown has one); a dataset with
// `hideInLegend: true` is left out of the legend.
export default class extends Controller {
  static values = { type: String, data: Object, options: Object, nowLine: Boolean, timeStart: Number, timeStep: Number }

  connect() {
    const options = Object.assign({ responsive: true, maintainAspectRatio: false }, this.optionsValue || {})
    options.plugins = options.plugins || {}
    const tooltip = options.plugins.tooltip = options.plugins.tooltip || {}
    tooltip.callbacks = Object.assign({
      title: (items) => (items.length && items.every((item) => item.raw?.tip) ? "" : undefined),
      label: (context) => context.raw?.tip
    }, tooltip.callbacks)
    const legendLabels = ((options.plugins.legend = options.plugins.legend || {}).labels = options.plugins.legend.labels || {})
    legendLabels.filter = legendLabels.filter || ((item, data) => !data.datasets[item.datasetIndex]?.hideInLegend)
    if (this.nowLineValue) {
      options.plugins.annotation = options.plugins.annotation || { annotations: {} }
      options.plugins.annotation.annotations.now = this.nowAnnotation()
      this.timer = setInterval(() => {
        this.chart.options.plugins.annotation.annotations.now = this.nowAnnotation()
        this.chart.update("none")
      }, 60_000)
    }
    this.chart = new Chart(this.element, { type: this.typeValue || "bar", data: this.dataValue, options })
  }

  disconnect() {
    clearInterval(this.timer)
    this.chart?.destroy()
  }

  nowAnnotation() {
    let x = Date.now()
    if (this.hasTimeStepValue && this.timeStepValue > 0) {
      x = (x - this.timeStartValue) / this.timeStepValue
      const last = (this.dataValue.labels || []).length - 1
      if (x < 0 || x > last) return { type: "line", display: false }
    }
    return { type: "line", xMin: x, xMax: x, borderColor: "#ef4444", borderWidth: 1.5,
             label: { display: true, content: "now", position: "start", font: { size: 10 } } }
  }
}
