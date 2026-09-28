class TelescopesController < ApplicationController
  def index
    @telescopes = policy_scope(Telescope).order(:name)
  end

  def show
    @telescope = policy_scope(Telescope).find_by!(slug: params[:id])
    authorize @telescope, :show?
  end
end
