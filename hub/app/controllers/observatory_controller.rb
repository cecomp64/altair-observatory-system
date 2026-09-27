# The observatory at a glance for every member: is each telescope operating
# tonight, what is it imaging, and how did the last nights go.
class ObservatoryController < ApplicationController
  def index
    telescopes = policy_scope(Telescope).active.includes(:processing_nodes).order(:name)
    @statuses = ObservatoryStatus.for(current_user, telescopes: telescopes)
  end
end
