# frozen_string_literal: true

# Masters follow their project's visibility (§12), like frames.
class DataProductPolicy < ApplicationPolicy
  def download?
    user.admin? || (record.project && ProjectPolicy.new(user, record.project).show?)
  end
end
