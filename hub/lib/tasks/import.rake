namespace :import do
  desc 'Import an astrophotography-database file: bin/rails "import:astrodb[/path/database.db,user@example.com,telescope=SLUG]"'
  task :astrodb, [ :path, :email, :telescope ] => :environment do |_, args|
    abort "usage: bin/rails \"import:astrodb[/path/database.db,user@example.com,telescope=SLUG]\"" if args[:path].blank? || args[:email].blank?

    user = User.find_by(email: args[:email]) or abort "No user #{args[:email]}"
    slug = args[:telescope].to_s.sub(/\Atelescope=/, "").presence
    telescope = slug && (Telescope.find_by(slug: slug) or abort "No telescope #{slug}")
    report = Imports::AstroDb.new(args[:path], user: user, telescope: telescope, showcases_dir: ENV["SHOWCASES_DIR"]).run
    puts report
  end
end
