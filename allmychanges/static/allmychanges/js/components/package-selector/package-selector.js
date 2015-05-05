var Package = require('./package.js')
var metrika = require('../metrika.js')

module.exports = React.createClass({
    getInitialState: function () {
        return {packages: [],
                tracked: []};
    },
    load_package: function () {
        $.ajax({
            url: this.props.url,
            dataType: 'json',
            success: function(data) {
                var results = data.results;
                if (results.length > 0) {
                    this.setState({package: results[0]});
                } else {
                    // TODO: somethings
                }
            }.bind(this),
            error: function(xhr, status, err) {
                console.error(this.props.url, status, err.toString());
            }.bind(this)
        });

    },
    componentDidMount: function() {
        this.load_package();
    },
    render: function() {
        var track_handler = function (changelog) {
            var tracked = this.state.tracked;
            tracked[tracked.length] = changelog;

            if (tracked.length == 1) {
                metrika.reach_goal('LAND-TRACK-1');
            }
            if (tracked.length == 3) {
                metrika.reach_goal('LAND-TRACK-3');
            }
            if (tracked.length == 5) {
                metrika.reach_goal('LAND-TRACK-5');
            }

            this.setState({tracked: tracked});
            this.load_package();
        }.bind(this);

        var skip_handler = function () {
            this.load_package();
        }.bind(this);

        var pkg_obj;
        var pkg = this.state.package;
        if (pkg !== undefined) {
            pkg_obj = <Package key={pkg.id}
                           changelog_id={pkg.id}
                           namespace={pkg.namespace}
                           name={pkg.name}
                           description={pkg.description}
                           versions={pkg.versions}
                           track_handler={track_handler}
                           skip_handler={skip_handler}/>;
        }

        var tracked = _.map(this.state.tracked,
                            function (changelog) {
                                return (
            <li key={changelog.id}>{changelog.namespace}/{changelog.name}</li>);});

        var tracked_msg;
        if (tracked.length > 0) {
            tracked_msg = (<div className="package-selector__tracked-msg">
                <p>You are following these packages:</p>
                <ul>{tracked}</ul>
                <p>To receive notifications on future releases, please, login with&nbsp;<a className="button _good _large" href="/login/github/"><i className="fa fa-github fa-lg"></i> GitHub</a> or <a className="button _good _large" href="/login/twitter/"><i className="fa fa-twitter fa-lg"></i> Twitter</a></p>
            </div>);
        }
                                        
        return (
            <div className="package-selector">
                {pkg_obj}
                {tracked_msg}
            </div>
        );
    }
});
