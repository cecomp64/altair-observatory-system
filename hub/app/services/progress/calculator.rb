module Progress
  # Project roll-ups (port of astrophotography-database project_service.py):
  # goal seconds per filter = sum of exposure_seconds x desired_count across
  # the project's targets; actual seconds per counter basis; % per filter
  # capped at 100; overall % is the mean across goal filters (§4.3).
  class Calculator
    BASES = {
      "acquired" => :completed_count,
      "collected" => :collected_count,
      "integrated" => :integrated_count
    }.freeze

    FilterRow = Struct.new(:filter, :goal_seconds, :actual_seconds, keyword_init: true) do
      def percent(basis)
        goal = goal_seconds.to_f
        goal.zero? ? 0.0 : [ actual_seconds[basis].to_f / goal * 100, 100.0 ].min.round(1)
      end
    end

    def initialize(project)
      @project = project
    end

    def plans
      @plans ||= @project.targets.flat_map(&:exposure_plans)
    end

    # [FilterRow] sorted by filter name.
    def by_filter
      @by_filter ||= plans.group_by(&:filter).map do |filter, filter_plans|
        FilterRow.new(
          filter: filter,
          goal_seconds: filter_plans.sum { |p| p.exposure_seconds.to_f * p.desired_count },
          actual_seconds: BASES.transform_values do |counter|
            filter_plans.sum { |p| p.exposure_seconds.to_f * [ p.public_send(counter), p.desired_count ].min }
          end
        )
      end.sort_by(&:filter)
    end

    def overall_percent(basis = @project.completion_basis)
      rows = by_filter
      return 0.0 if rows.empty?

      (rows.sum { |row| row.percent(basis) } / rows.size).round(1)
    end

    # The goal filter furthest behind on the project's basis: what to shoot next.
    def recommended_filter
      by_filter.min_by { |row| row.percent(@project.completion_basis) }&.then { |row| row.percent(@project.completion_basis) < 100 ? row.filter : nil }
    end
  end
end
