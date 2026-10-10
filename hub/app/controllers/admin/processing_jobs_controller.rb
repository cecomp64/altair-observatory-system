module Admin
  # Altair's jobs as it reports them (PUT /api/v1/processing/jobs/:altair_id).
  # A summary only; Altair keeps the full job table.
  class ProcessingJobsController < BaseController
    PER_PAGE = 50

    def index
      @nodes = ProcessingNode.order(:name)
      @node = @nodes.find { |n| n.name == params[:node] }
      @kind = params[:kind].presence_in(ProcessingJob::KINDS)
      @status = params[:status].presence_in(ProcessingJob::STATUSES + %w[active])

      scope = ProcessingJob.all
      scope = scope.where(processing_node: @node) if @node
      scope = scope.where(kind: @kind) if @kind
      @status_counts = scope.group(:status).count
      scope = scope.where(status: @status == "active" ? ProcessingJob::ACTIVE_STATUSES : @status) if @status

      @pagy, @jobs = pagy(scope.includes(:processing_node, :target).order(updated_at: :desc, id: :desc), limit: PER_PAGE)
    end

    def show
      @job = ProcessingJob.includes(:processing_node, :target).find(params[:id])
    end
  end
end
