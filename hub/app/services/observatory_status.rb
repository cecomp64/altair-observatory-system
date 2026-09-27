# What every member sees on /observatory: per telescope, whether it is
# operating tonight and what it's doing, from what the Hub already has (roof
# and session events from the rig agent, frames from Altair, heartbeats, and
# an admin's operating status). Weather is not here yet.
class ObservatoryStatus
  RECENT_NIGHTS = 7
  NOW_IMAGING_WINDOW = 45.minutes

  Night = Struct.new(:date, :opened_at, :closed_at, :hours_open, :lights, :light_hours, keyword_init: true)

  class TelescopeStatus
    attr_reader :telescope, :viewer, :date, :now

    def initialize(telescope, viewer:, now: Time.current)
      @telescope = telescope
      @viewer = viewer
      @now = now
      @date = telescope.night_for(now)
    end

    def astro_night
      @astro_night ||= Astro::Visibility.new(Astro::Site.for(telescope)).night(date)
    end

    def darkness
      astro_night.darkness
    end

    def moon_percent
      (astro_night.moon_illumination * 100).round
    end

    def observing_nights
      @observing_nights ||= telescope.observing_nights.where(night: date).to_a
    end

    def opened_at
      observing_nights.filter_map(&:roof_open_at).min
    end

    def closed_at
      return nil if observing_nights.any?(&:imaging_now?)

      observing_nights.filter_map { |n| n.roof_closed_at || n.session_end_at }.max
    end

    # maintenance / offline (an admin said so), imaging, finished (opened and
    # closed tonight), waiting (before darkness), closed (dark but not open),
    # day (after dawn, never opened).
    def state
      @state ||= compute_state
    end

    def lights_tonight
      @lights_tonight ||= Frame.lights.counted.where(telescope: telescope, night: date)
    end

    def light_count
      @light_count ||= lights_tonight.count
    end

    def light_hours
      (lights_tonight.sum(:exposure_s).to_f / 3600).round(2)
    end

    def last_frame
      @last_frame ||= lights_tonight.order(date_obs: :desc).includes(target: :project).first
    end

    # The target of the newest frame, while the roof is open and it is recent.
    def current_target
      return nil unless state == "imaging" && last_frame && last_frame.date_obs >= now - NOW_IMAGING_WINDOW

      last_frame.target
    end

    # [target, lights, hours] for each target imaged tonight, most first.
    def targets_tonight
      @targets_tonight ||= begin
        rows = lights_tonight.where.not(target_id: nil).group(:target_id)
                             .pluck(:target_id, Arel.sql("count(*)"), Arel.sql("coalesce(sum(exposure_s), 0)"))
        targets = Target.where(id: rows.map(&:first)).includes(:project).index_by(&:id)
        rows.filter_map { |id, n, s| [ targets[id], n, (s.to_f / 3600).round(2) ] if targets[id] }.sort_by { |row| -row[1] }
      end
    end

    def queue_size
      @queue_size ||= telescope.targets.schedulable.count
    end

    def queue_members
      @queue_members ||= telescope.targets.schedulable.distinct.count(:user_id)
    end

    def recent_nights
      @recent_nights ||= begin
        dates = (1..RECENT_NIGHTS).map { |i| date - i }
        opened = telescope.observing_nights.where(night: dates).group_by(&:night)
        lights = Frame.lights.counted.where(telescope: telescope, night: dates).group(:night)
                      .pluck(:night, Arel.sql("count(*)"), Arel.sql("coalesce(sum(exposure_s), 0)")).to_h { |d, n, s| [ d, [ n, s ] ] }
        dates.map do |d|
          records = opened[d] || []
          open = records.filter_map(&:roof_open_at).min
          close = records.filter_map { |r| r.roof_closed_at || r.session_end_at }.max
          count, seconds = lights[d] || [ 0, 0 ]
          Night.new(date: d, opened_at: open, closed_at: close,
                    hours_open: open && close && close > open ? ((close - open) / 3600).round(1) : nil,
                    lights: count, light_hours: (seconds.to_f / 3600).round(2))
        end
      end
    end

    def rig_heartbeat_at
      telescope.worker_last_heartbeat_at
    end

    def rig_last_command
      telescope.worker_status.is_a?(Hash) ? telescope.worker_status.dig("status", "last_command") : nil
    end

    def processing_nodes
      telescope.processing_nodes.active.to_a
    end

    def compute_state
      return telescope.operating_status unless telescope.operational?
      return "imaging" if observing_nights.any?(&:imaging_now?)
      return "finished" if opened_at
      return "waiting" if darkness.nil? || now < darkness.begin

      now <= darkness.end ? "closed" : "day"
    end

    # Other members' private projects show as "a member's target".
    def visible?(target)
      target && Pundit.policy(viewer, target).show?
    end
  end

  # Open /observatory pages refresh (Turbo morph) when anything here changes.
  def self.broadcast
    Turbo::StreamsChannel.broadcast_refresh_later_to(:observatory)
  end

  def self.for(viewer, telescopes:, now: Time.current)
    telescopes.map { |t| TelescopeStatus.new(t, viewer: viewer, now: now) }
  end
end
