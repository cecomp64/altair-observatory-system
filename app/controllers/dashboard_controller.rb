class DashboardController < ApplicationController
  def index
    @targets = policy_scope(Target).includes(:telescope, :exposure_plans).order(created_at: :desc)
    @status_counts = policy_scope(Target).group(:status).count
    @active_targets = @targets.select { |t| Target::SCHEDULABLE_STATUSES.include?(t.status) }
    @recent_events = TargetEvent.joins(:target)
                                 .where(targets: { id: @targets.map(&:id) })
                                 .includes(:target)
                                 .recent_first
                                 .limit(10)
  end
end
