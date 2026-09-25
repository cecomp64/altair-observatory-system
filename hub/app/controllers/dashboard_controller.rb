class DashboardController < ApplicationController
  def index
    @projects = current_user.projects.where(status: %w[planning active paused])
                            .includes(targets: [ :exposure_plans, :telescope ]).recent_first.limit(6)
    @targets = policy_scope(Target).where(user: current_user).schedulable
                                   .includes(:exposure_plans, :project, :telescope).to_a
    telescopes = Telescope.active.where(id: @targets.map(&:telescope_id)).order(:name).to_a
    telescopes = policy_scope(Telescope).active.order(:name).limit(1).to_a if telescopes.empty?
    @tonight = telescopes.map { |t| TonightPlanner.new(t, targets: @targets) }
    @my_object_ids = @targets.filter_map(&:astro_object_id)
    @imaging_now = ObservingNight.where(night: [ Date.current, Date.yesterday ]).where.not(roof_open_at: nil)
                                 .where(roof_closed_at: nil, session_end_at: nil).includes(:telescope)
                                 .select(&:imaging_now?).map(&:telescope).uniq
    @my_issues = policy_scope(ProcessingIssue).open.where(project: current_user.projects).recent_first.limit(5).includes(:target)
    @nodes = current_user.admin? ? ProcessingNode.active.includes(:telescopes).order(:name) : []
    @recent_events = TargetEvent.joins(:target)
                                .where(targets: { user_id: current_user.id })
                                .includes(:target)
                                .recent_first
                                .limit(10)
  end
end
