module Admin
  class DashboardController < BaseController
    def index
      @telescopes = Telescope.order(:name)
      @targets_by_status = Target.group(:status).count
      @recent_targets = Target.includes(:user, :telescope).order(created_at: :desc).limit(10)
      @nodes = ProcessingNode.active.includes(:telescopes).order(:name)
      @open_issues = ProcessingIssue.open.count
      @unassigned = Frame.unassigned.count
    end
  end
end
