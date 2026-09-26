class TargetsController < ApplicationController
  before_action :set_target, only: [ :show, :cancel ]

  def index
    @targets = policy_scope(Target).includes(:telescope, :exposure_plans).order(created_at: :desc)
  end

  def show
    authorize @target
    @exposure_plans = @target.exposure_plans
    @masters = @target.data_products.masters.current.recent_first
                      .includes(preview_attachment: :blob, thumbnail_attachment: :blob)
    @preview = @masters.find { |m| m.multi_night_master? && m.preview.attached? } || @masters.find { |m| m.preview.attached? }
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
