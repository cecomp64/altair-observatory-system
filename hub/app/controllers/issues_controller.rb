class IssuesController < ApplicationController
  before_action :set_issue, only: [ :show, :waive, :approve, :deny ]

  def index
    authorize ProcessingIssue
    @status = params[:status].presence_in(%w[open resolved waived all]) || "open"
    scope = policy_scope(ProcessingIssue).includes(:target, :project, :optical_train).recent_first
    scope = scope.where(status: @status) unless @status == "all"
    @pagy, @issues = pagy(scope, limit: 50)
  end

  def show
    authorize @issue
    @commands = ProcessingCommand.where(processing_node: @issue.processing_node)
                                 .where("payload->>'fingerprint' = ?", @issue.fingerprint).recent_first
  end

  def waive
    authorize @issue
    note = params[:note].to_s.strip
    return redirect_to(issue_path(@issue), alert: "Say why the issue can be waived.") if note.blank?

    command(:issue_waive, fingerprint: @issue.fingerprint, note: "#{note} — #{current_user.display_name}")
    redirect_to issue_path(@issue), notice: "Waive sent to Altair."
  end

  def approve
    authorize @issue
    command(:approve_fetch, fingerprint: @issue.fingerprint)
    redirect_to issue_path(@issue), notice: "Fetch approved; Altair will start it."
  end

  def deny
    authorize @issue, :approve?
    command(:deny_fetch, fingerprint: @issue.fingerprint)
    redirect_to issue_path(@issue), notice: "Fetch denied."
  end

  private

  def set_issue
    @issue = ProcessingIssue.find(params[:id])
  end

  # Commands go to the node that raised the issue.
  def command(kind, payload)
    @issue.processing_node.processing_commands.create!(kind: kind.to_s, payload: payload, requested_by: current_user, target: @issue.target)
  end
end
