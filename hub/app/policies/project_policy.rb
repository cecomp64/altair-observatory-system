# frozen_string_literal: true

class ProjectPolicy < ApplicationPolicy
  def index?
    true
  end

  # Club members can see projects their owner shared with the club.
  def show?
    owner? || user.admin? || record.visibility_club?
  end

  def update?
    owner? || user.admin?
  end

  class Scope < Scope
    def resolve
      return scope.all if user.admin?

      scope.where(user: user).or(scope.where(visibility: "club"))
    end
  end

  private

  def owner?
    record.user_id == user.id
  end
end
