class TargetsController < ApplicationController
  before_action :set_target, only: [ :show, :cancel ]

  def index
    @targets = policy_scope(Target).includes(:telescope, :exposure_plans).order(created_at: :desc)
  end

  def show
    authorize @target
    @exposure_plans = @target.exposure_plans
    @files = @target.data_products.legacy.recent_first
    @preview = @files.preview.first
    @events = @target.target_events.recent_first.limit(20)
  end

  def cancel
    authorize @target
    @target.update!(status: :cancelled)
    redirect_to @target, notice: "Target cancelled."
  end

  private

  def set_target
    @target = Target.find(params[:id])
  end
end
