# frozen_string_literal: true

# Frames follow their project's visibility (§12): the owner, admins, and
# club members for club projects. Unassigned frames are for admins, who
# route them to targets.
class FramePolicy < ApplicationPolicy
  def index? = true

  def show?
    user.admin? || (record.project && ProjectPolicy.new(user, record.project).show?)
  end

  def assign? = user.admin?
  def unassigned? = user.admin?

  class Scope < Scope
    def resolve
      return scope.all if user.admin?

      scope.where(project_id: ProjectPolicy::Scope.new(user, Project).resolve.select(:id))
    end
  end
end
