module Progress
  # Recomputes the Hub-owned counters of a target's plans (§4.3) from frames
  # and data products, so they always equal a from-scratch count:
  #
  # - collected: lights linked to the plan, not invalid
  # - usable: lights used in a current final night master (listed in its
  #   metrics.frame_sha256s, or marked processed by Altair)
  # - integrated: lights in the current multi-night master for the filter
  #
  # With completion basis "integrated", a target completes here.
  class Recompute
    def self.targets(ids)
      Target.where(id: ids).includes(:exposure_plans, :project).find_each { |t| new(t).run }
    end

    def initialize(target)
      @target = target
    end

    def run
      frames = @target.frames.lights.counted.where.not(exposure_plan_id: nil).pluck(:exposure_plan_id, :sha256, :status, :exposure_s)
      by_plan = frames.group_by(&:first)
      night_masters = @target.data_products.night_master.current.to_a
      multi = @target.data_products.multi_night_master.current.order(version: :desc, id: :desc).group_by(&:filter).transform_values(&:first)
      used_in_nights = night_masters.flat_map { |p| Array(p.metrics["frame_sha256s"]) }.to_set

      @target.exposure_plans.each do |plan|
        rows = by_plan[plan.id] || []
        usable = rows.count { |_, sha, status, _| used_in_nights.include?(sha) || status == "processed" }
        integrated_rows = integrated_rows(rows, multi[plan.filter], plan)
        plan.update_columns(
          collected_count: rows.size, usable_count: usable,
          integrated_count: integrated_rows.size, integrated_seconds: integrated_rows.sum { |r| r[3].to_f },
          updated_at: Time.current
        )
      end
      complete_if_integrated
    end

    private

    def integrated_rows(rows, master, plan)
      return [] unless master

      listed = Array(master.metrics["frame_sha256s"]).to_set
      return rows.select { |_, sha, _, _| listed.include?(sha) } if listed.any?

      # No frame list: attribute the master's frame count to the only plan of
      # that filter (with several plans it can't be split, so count none).
      same_filter = @target.exposure_plans.count { |p| p.filter == plan.filter }
      same_filter == 1 ? rows.first(master.metrics["frames"].to_i) : []
    end

    def complete_if_integrated
      return unless @target.project.completion_integrated?
      return if @target.completed? || @target.cancelled? || !@target.reload.fully_captured?

      @target.update!(status: :completed)
      @target.target_events.create!(event_type: :status_changed, payload: { status: "completed", basis: "integrated" })
    end
  end
end
